# ------------------------------------------------------------------------------
# 
#    CNOTExRec trainer. Uses an RNN w/ 4 LSTM cells to train X & Z at same time.
#
#    Copyright (C) 2017 Pooya Ronagh
# 
# ------------------------------------------------------------------------------

from builtins import range
import numpy as np
import tensorflow as tf
import sys
from util import y2indicator

# The CSS code generator matrix
G= np.matrix([[0,0,0,1,1,1,1], \
              [0,1,1,0,0,1,1], \
              [1,0,1,0,1,0,1]]).astype(np.int32)

class Data:

    def __init__(self, data):
        self.input= {}
        self.input['X']= \
    np.concatenate((data['synX12'], data['synX34']), axis= 1).reshape(-1, 2, 6)
        self.input['Z']= \
    np.concatenate((data['synZ12'], data['synZ34']), axis= 1).reshape(-1, 2, 6)
        self.output= {}
        self.output['X3']= data['errX3']
        self.output['X4']= data['errX4']
        self.output['Z3']= data['errZ3']
        self.output['Z4']= data['errZ4']
        X12_ind=y2indicator(data['errX12'],2**14).astype(np.float32)
        X34_ind=y2indicator(data['errX34'],2**14).astype(np.float32)
        Z12_ind=y2indicator(data['errZ12'],2**14).astype(np.float32)
        Z34_ind=y2indicator(data['errZ34'],2**14).astype(np.float32)
        self.output_ind= {}
        self.output_ind['X']= \
    np.concatenate((X12_ind, X34_ind), axis= 1).reshape(-1, 2 * 2**14)
        self.output_ind['Z']= \
    np.concatenate((Z12_ind, Z34_ind), axis= 1).reshape(-1, 2 * 2**14)

def io_data_factory(data, test_size):

    train_data_arg = {key:data[key][:-test_size,] for key in data.keys()}
    test_data_arg  = {key:data[key][-test_size:,] for key in data.keys()}
    train_data = Data(train_data_arg)
    test_data = Data(test_data_arg)
    return train_data, test_data

def find_logical_fault(recovery, err):

    p_binary= '{0:07b}'.format(recovery)
    t_binary= '{0:07b}'.format(int(err))
    err_list= [int(a!=b) for a, b in zip(p_binary, t_binary)]
    err= np.matrix(err_list).astype(np.int32)
    syndrome= np.dot(G, err.transpose()) % 2
    correction_index= np.dot([[4, 2, 1]], syndrome) - 1
    correction = y2indicator(correction_index, 7)
    coset= (err + correction) % 2
    logical_err= np.sum(coset) % 2
    return logical_err

def num_logical_fault(prediction, test_data, test_size):

    error_keys= ['X3', 'X4', 'Z3', 'Z4']
    error_counter= 0.0
    for i in range(test_size):
        pred= {}
        pred['X3']= prediction['X'][i] // 2**7
        pred['X4']= prediction['X'][i] % 2**7
        pred['Z3']= prediction['Z'][i] // 2**7
        pred['Z4']= prediction['Z'][i] % 2**7
        for key in error_keys:
            if (find_logical_fault(pred[key], test_data.output[key][i])):
                error_counter+=1
                break
    return error_counter/test_size
    
def get_data(filename):

    data= {}
    data['synX12']= []
    data['synX34']= []
    data['synZ12']= []
    data['synZ34']= []
    data['errX12']= []
    data['errX34']= []
    data['errZ12']= []
    data['errZ34']= []
    data['errX3']= []
    data['errX4']= []
    data['errZ3']= []
    data['errZ4']= []
    with open(filename) as file:
        first_line = file.readline();
        p, lu_avg, lu_std, data_size = first_line.split(' ')
        p= float(p)
        lu_avg= float(lu_avg)
        lu_std= float(lu_std)
        data_size= int(data_size)
        for line in file.readlines():
            line_list= line.split(' ')
            data['synX12'].append([bit for bit in ''.join(line_list[0:2])])
            data['synX34'].append([bit for bit in ''.join(line_list[2:4])])
            data['synZ12'].append([bit for bit in ''.join(line_list[8:10])])
            data['synZ34'].append([bit for bit in ''.join(line_list[10:12])])
            data['errX3'].append([int(line_list[6],2)])
            data['errX4'].append([int(line_list[7],2)])
            data['errZ3'].append([int(line_list[14],2)])
            data['errZ4'].append([int(line_list[15],2)])
            data['errX12'].append([int(''.join(line_list[4:6]),2)])
            data['errX34'].append([int(''.join(line_list[6:8]),2)])
            data['errZ12'].append([int(''.join(line_list[12:14]),2)])
            data['errZ34'].append([int(''.join(line_list[14:16]),2)])
    for key in data.keys():
        data[key]= np.array(data[key]).astype(np.float32)
    return data, p, lu_avg, lu_std, data_size

def train(filename, param):

    test_fraction= param['data']['test fraction']
    batch_size= param['data']['batch size']
    learning_rate= param['opt']['learning rate']
    num_iterations= param['opt']['iterations']
    momentum_val= param['opt']['momentum']
    decay_rate= param['opt']['decay']
    verbose= param['usr']['verbose']
    num_hidden= param['nn']['num hidden'] 

    output= {}
    output['data']= {}
    output['opt']= {}
    output['res']= {}

    # Read data and figure out how much null syndromes to assume for error_scale
    print("Reading data from " + filename)
    output['data']['path']= filename

    raw_data, p, lu_avg, lu_std, data_size = get_data(filename)
    output['res']['p']= p
    output['res']['lu avg']= lu_avg
    output['res']['lu std']= lu_std

    total_size= np.shape(raw_data['synX12'])[0]
    test_size= int(test_fraction * total_size)
    train_size= total_size - test_size
    n_batches = train_size // batch_size
    error_scale= 1.0*total_size/data_size
    output['data']['fault scale']= error_scale
    output['data']['total data size']= total_size
    output['data']['test set size']= test_size
    output['opt']['batch size']= batch_size
    output['opt']['number of batches']= n_batches

    num_classes= 2**14
    num_inputs= 2
    input_size= 6

    train_data, test_data = io_data_factory(raw_data, test_size)

    prediction= {}
    train_keys= ['X', 'Z']
    for key in train_keys:
        tf.reset_default_graph()

        x = tf.placeholder(tf.float32, [None, num_inputs, input_size])
        y = tf.placeholder(tf.float32, [None, num_inputs, num_classes])
        lstm = tf.contrib.rnn.LSTMCell(num_hidden)
        lstmOut, _ = tf.nn.dynamic_rnn(lstm, x, dtype=tf.float32)
        lstmOut= tf.reshape(lstmOut, [-1, num_inputs * num_hidden])
        W= tf.Variable(tf.random_normal([num_inputs * num_hidden, \
            num_inputs * num_classes]))
        b= tf.Variable(tf.random_normal([num_inputs * num_classes]))
        logits= tf.matmul(lstmOut, W) + b
    
        loss= tf.nn.softmax_cross_entropy_with_logits(logits=logits, labels=y)
        cost= tf.reduce_sum(loss)

        train = tf.train.RMSPropOptimizer(learning_rate, \
            decay=decay_rate, momentum=momentum_val).minimize(cost)

        predict= tf.argmax(logits[:, -num_classes:], 1)
        init = tf.global_variables_initializer()

        with tf.Session() as session:
            session.run(init)

            for i in range(num_iterations):
                for j in range(n_batches):
                    beg= j * batch_size
                    end= j * batch_size + batch_size
                    
                    feed_dict={}
                    feed_dict[x]= train_data.input[key][beg:end,]
                    feed_dict[y]= train_data.output_ind[key][beg:end,]
                    session.run(train, feed_dict)
            
            prediction[key]= session.run(predict, \
                feed_dict= {x: test_data.input[key]})

    avg= num_logical_fault(prediction, test_data, test_size)

    output['res']['nn avg'] = error_scale * avg
    output['res']['nn std'] = 0

    return output

'''
__main__():
  Args: 
    json parameter file,
    data folder.
'''

if __name__ == '__main__':

    import sys
    import os
    import json
    from time import localtime, strftime

    with open(sys.argv[1]) as paramfile:
        param = json.load(paramfile)
    datafolder= sys.argv[2]

    output= []

    for filename in os.listdir(datafolder):
        output.append(train(datafolder + filename, param))

    outfilename = strftime("%Y-%m-%d-%H-%M-%S", localtime())
    f = open('Reports/' + outfilename + '.json', 'w')
    f.write(json.dumps(output, indent=2))
    f.close()
