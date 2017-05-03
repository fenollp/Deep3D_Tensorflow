import tensorflow as tf

import numpy as np
from functools import reduce
import os.path
import selection

VGG_MEAN = [103.939, 116.779, 123.68]


class Deep3Dnet:
    """
    A trainable version deep3dnet.
    """

    def __init__(self, deep3d_path=None, trainable=True, dropout=0.5):
        if deep3d_path is not None:
            if os.path.isfile(deep3d_path):
                self.data_dict = np.load(deep3d_path, encoding='latin1').item()
            
                #removing pre-trained weights for fully connected layers so they'll be re-initialized
                del self.data_dict[u'fc6']
                del self.data_dict[u'fc7']
                del self.data_dict[u'fc8']
            
            else:
                self.data_dict = None
        else:
            self.data_dict = None

        self.var_dict = {}
        self.trainable = trainable
        self.dropout = dropout


    def build(self, rgb, train_mode=None):
        """
        load variable from npy to build the VGG

        :param rgb: rgb image [batch, height, width, 3] values scaled [0, 1]
        :param train_mode: a bool tensor, usually a placeholder: if True, dropout will be turned on
        """
        with tf.variable_scope("Pre_Processing"):
            rgb_scaled = rgb * 255.0

            # Convert RGB to BGR
            red, green, blue = tf.split(axis=3, num_or_size_splits=3, value=rgb_scaled)
            assert red.get_shape().as_list()[1:] == [160, 288, 1]
            assert green.get_shape().as_list()[1:] == [160, 288, 1]
            assert blue.get_shape().as_list()[1:] == [160, 288, 1]
            bgr = tf.concat(axis=3, values=[
                blue - VGG_MEAN[0],
                green - VGG_MEAN[1],
                red - VGG_MEAN[2],
            ])
            assert bgr.get_shape().as_list()[1:] == [160, 288, 3]

        # Convolution Stages
        self.conv1_1 = self.conv_layer(bgr, 3, 64, "conv1_1",                train_mode, trainable=0)
        self.conv1_2 = self.conv_layer(self.conv1_1, 64, 64, "conv1_2",      train_mode, tracking=1,trainable=0)
        self.pool1 = self.max_pool(self.conv1_2, 'pool1')

        self.conv2_1 = self.conv_layer(self.pool1, 64, 128, "conv2_1",       train_mode,trainable=0)
        self.conv2_2 = self.conv_layer(self.conv2_1, 128, 128, "conv2_2",    train_mode, tracking=1,trainable=0)
        self.pool2 = self.max_pool(self.conv2_2, 'pool2')

        self.conv3_1 = self.conv_layer(self.pool2, 128, 256, "conv3_1",      train_mode,trainable=0)
        self.conv3_2 = self.conv_layer(self.conv3_1, 256, 256, "conv3_2",    train_mode,trainable=0)
        self.conv3_3 = self.conv_layer(self.conv3_2, 256, 256, "conv3_3",    train_mode,trainable=0)
        self.conv3_4 = self.conv_layer(self.conv3_3, 256, 256, "conv3_4",    train_mode, tracking=1,trainable=0)
        self.pool3 = self.max_pool(self.conv3_4, 'pool3')

        self.conv4_1 = self.conv_layer(self.pool3, 256, 512, "conv4_1",      train_mode,trainable=0)
        self.conv4_2 = self.conv_layer(self.conv4_1, 512, 512, "conv4_2",    train_mode,trainable=0)
        self.conv4_3 = self.conv_layer(self.conv4_2, 512, 512, "conv4_3",    train_mode,trainable=0)
        self.conv4_4 = self.conv_layer(self.conv4_3, 512, 512, "conv4_4",    train_mode, tracking=1,trainable=0)
        self.pool4 = self.max_pool(self.conv4_4, 'pool4')

        self.conv5_1 = self.conv_layer(self.pool4, 512, 512, "conv5_1",      train_mode, trainable=0)
        self.conv5_2 = self.conv_layer(self.conv5_1, 512, 512, "conv5_2",    train_mode, trainable=0)
        self.conv5_3 = self.conv_layer(self.conv5_2, 512, 512, "conv5_3",    train_mode, trainable=0)
        self.conv5_4 = self.conv_layer(self.conv5_3, 512, 512, "conv5_4",    train_mode, tracking=1,trainable=0)
        self.pool5 = self.max_pool(self.conv5_4, 'pool5')

        # FC Layers + Relu + Dropout
        # First Dimensions: 23040=((160//(2**5))*(288//(2**5)))*512
        self.fc6 = self.affine_layer(self.pool5, 23040, 4096, "fc6",         train_mode, tracking=1) 
        self.fc7 = self.affine_layer(self.fc6, 4096, 4096, "fc7",            train_mode, tracking=1)
        self.fc8 = self.affine_layer(self.fc7, 4096, 33*9*5, "fc8",          train_mode, tracking=1)
        
        # Upscaling last branch
        with tf.variable_scope("FC_rs"):
            self.fc_RS = tf.reshape(self.fc8,[-1,5,9,33])
        
        scale = 16
        self.up5 = self.deconv_layer(self.fc_RS, 33, 33, scale, 0, 'up5',    train_mode, tracking=1)

        # Combine and x2 Upsample
        self.up_sum = self.up5
    
        scale = 2
        self.up = self.deconv_layer(self.up_sum, 33, 33, scale, 0, 'up',     train_mode, tracking=1)
        self.up_conv = self.conv_layer(self.up, 33, 33, "up_conv",           train_mode, tracking=1)
        
        # Tracking presoftmax activation
        with tf.name_scope('up_conv_act'):
            variable_summaries(self.up_conv)
        
        
        # Add + Mask + Selection
        with tf.variable_scope("mask_softmax"):
            self.mask = tf.nn.softmax(self.up_conv)

        with tf.name_scope('mask_act'):
            variable_summaries(self.up_conv)
        

        self.prob  = selection.select(self.mask, rgb)

        with tf.name_scope('prob'):
            variable_summaries(self.prob)

        # Clear out init dictionary
        self.data_dict = None
   
        
    # =========== Macro Layers =========== #
    def max_pool(self, bottom, name):
        return tf.nn.max_pool(bottom, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME', name=name)
    
    def batch_norm(self, bottom, train_mode, name='bn'):
        return tf.contrib.layers.batch_norm(bottom, center=True, scale=True, is_training=train_mode, scope=name)
    

    def conv_layer(self, bottom, in_channels, out_channels, name,
                   train_mode, batchnorm=0, tracking=0, trainable=1):

        with tf.variable_scope(name):
            filters, biases = self.get_conv_var(3, in_channels, out_channels, name, trainable)
            conv = tf.nn.conv2d(bottom, filters, [1, 1, 1, 1], padding='SAME')
            bias = tf.nn.bias_add(conv, biases)
            
            if batchnorm == 1:
                 bias = batch_norm(bias, train_mode)
            
            relu = tf.nn.relu(bias)

            if tracking == 1:
                with tf.name_scope('filters'):
                    variable_summaries(filters)
                with tf.name_scope('biases'):
                    variable_summaries(biases)
                    
            return relu
    

    def deconv_layer(self, bottom, in_channels, out_channels, 
                     scale, bias, name,
                     train_mode, initialization='default', batchnorm=0, tracking = 0, trainable=1):
        
        with tf.variable_scope(name):
            N, H, W, C = bottom.get_shape().as_list()
            shape_output = [N, scale * (H - 1) + scale * 2 - scale, scale * (W - 1) + scale * 2 - scale, out_channels] 

            filters, biases = self.get_deconv_var(2*scale, in_channels, out_channels, bias, initialization, name, trainable)
            deconv = tf.nn.conv2d_transpose(bottom, filters, shape_output, [1, scale, scale, 1])

            if bias:
                deconv = tf.nn.bias_add(deconv, biases)

            if batchnorm == 1:
                 deconv = batch_norm(deconv, train_mode)
            
            relu = tf.nn.relu(deconv)

            if tracking == 1:
                with tf.name_scope('filters'):
                    variable_summaries(filters)
                if bias:
                    with tf.name_scope('biases'):
                        variable_summaries(biases)

            return relu

    def affine_layer(self, bottom, in_size, out_size, name,
                     train_mode, batchnorm=0, tracking=0, trainable=1):
        with tf.variable_scope(name):
            weights, biases = self.get_fc_var(in_size, out_size, name, trainable)
            x = tf.reshape(bottom, [-1, in_size])
            fc = tf.nn.bias_add(tf.matmul(x, weights), biases)
            
            if batchnorm == 1:
                fc = batch_norm(fc, train_mode)
        
            relu = tf.nn.relu(fc)
            
            if train_mode is not None and self.trainable:
                relu = tf.nn.dropout(relu, self.dropout)
            
            if tracking == 1:
                with tf.name_scope('weights'):
                    variable_summaries(weights)
                with tf.name_scope('biases'):
                    variable_summaries(biases)
           
            return relu 
        
        
    # ======= Get Var Functions =========== #
        
    # def get_bn_var(self, bottom, name):
    #     N, H, W, C = bottom.get_shape().as_list()
        
    #     initial_value = tf.truncated_normal([N, H, W, C], 0.0, 0.01)
    #     gamma = self.get_var(initial_value, name, 0, name + "_gamma")
    #     #del initial_value
        
    #     initial_value = tf.truncated_normal([1, H, W, C], 0.0, 0.01)
    #     beta = self.get_var(initial_value, name, 1, name + "_beta")
    #     h2 = tf.contrib.layers.batch_norm(h1, center=True, scale=True, is_training=phase, scope='bn')
        
    #     return gamma, beta
    
    def get_conv_var(self, filter_size, in_channels, out_channels,
                     name , trainable):

        initial_value = tf.truncated_normal([filter_size, filter_size, in_channels, out_channels], 0.0, 0.01)
        filters = self.get_var(initial_value, name, 0, name + "_filters", trainable)
        #del initial_value
        
        initial_value = tf.truncated_normal([out_channels], 0.0, 0.01)
        biases = self.get_var(initial_value, name, 1, name + "_biases", trainable)
        #del initial_value

        return filters, biases
    
    def get_deconv_var(self, filter_size, in_channels, out_channels, 
                       bias, initialization,
                       name, trainable):

        #Initializing to bilinear interpolation
        if initialization == 'bilinear':
            C = (2 * filter_size - 1 - (filter_size % 2))/(2*filter_size)
            initial_value = np.zeros([filter_size, filter_size, in_channels, out_channels])
            for i in xrange(filter_size):
                for j in xrange(filter_size):
                    initial_value[i, j] = (1-np.abs(i/(filter_size - C))) * (1-np.abs(j/(filter_size - C)))
            initial_value = tf.convert_to_tensor(initial_value, tf.float32)

        else:
            initial_value = tf.truncated_normal([filter_size,filter_size,in_channels,out_channels],0.0,0.01)


        filters = self.get_var(initial_value, name, 0, name + "_filters", trainable)
        
        biases = None
        if bias:
            initial_value = tf.truncated_normal([out_channels], 0.0, 0.01)
            biases = self.get_var(initial_value, name, 1, name + "_biases")

        #del initial_value
        return filters, biases

    def get_fc_var(self, in_size, out_size, 
                   name, trainable):
        #initialize all other weights with normal distribution with a standard deviation of 0.01
        initial_value = tf.truncated_normal([in_size, out_size], 0.0, 0.01)
        weights = self.get_var(initial_value, name, 0, name + "_weights", trainable)
        #del initial_value

        initial_value = tf.truncated_normal([out_size], 0.0, 0.01)
        biases = self.get_var(initial_value, name, 1, name + "_biases", trainable)
        #del initial_value


        return weights, biases
    

    
    def get_var(self, initial_value, name, idx, var_name, trainable):
        if self.data_dict is not None and name in self.data_dict:
            value = self.data_dict[name][idx]
        else:
            value = initial_value

        if self.trainable:
            var = tf.Variable(value, name=var_name, trainable=trainable)

        else:
            var = tf.constant(value, dtype=tf.float32, name=var_name)

        self.var_dict[(name, idx)] = var

        assert var.get_shape() == initial_value.get_shape()
       
        return var
    
    
    # =========== Util Functions ========= # 
    def save_npy(self, sess, npy_path="./deep3d-save.npy"):
        assert isinstance(sess, tf.Session)

        data_dict = {}

        for (name, idx), var in list(self.var_dict.items()):
            var_out = sess.run(var)
            if name not in data_dict:
                data_dict[name] = {}
            data_dict[name][idx] = var_out

        np.save(npy_path, data_dict)
        print(("file saved", npy_path))
        return npy_path

    def get_var_count(self):
        count = 0
        for v in list(self.var_dict.values()):
            count += reduce(lambda x, y: x * y, v.get_shape().as_list())
        return count

def variable_summaries(var):
    """Attach a lot of summaries to a Tensor (for TensorBoard visualization).
    https://www.tensorflow.org/get_started/summaries_and_tensorboard
    """
    
    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean', mean)
        tf.summary.histogram('histogram', var)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_mean(tf.square(var - mean)))
        tf.summary.scalar('stddev',tf.sqrt(tf.reduce_mean(tf.square(var - mean))))
        tf.summary.scalar('max', tf.reduce_max(var))
        tf.summary.scalar('min', tf.reduce_min(var))
    