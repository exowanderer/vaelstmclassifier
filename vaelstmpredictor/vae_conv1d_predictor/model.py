import joblib
import json
import numpy as np
import scipy.stats

from functools import partial, update_wrapper

from keras import backend as K
from keras.layers import Input, Lambda, concatenate, Dense
from keras.layers import Conv1D, MaxPooling1D
from keras.layers import UpSampling1D, Flatten, Reshape
from keras.layers import BatchNormalization, Dropout
from keras.losses import mean_squared_error
from keras.models import Model, Sequential
from keras.regularizers import l1_l2

ITERABLES = (list, tuple, np.array)

'''HERE WHERE I STARTED'''
# class CustomVariationalLayer(keras.Layer):
# 	def vae_loss(self, x, z_decoded, z_mean, z_log_var, kl_loss_coeff=5e-4):
# 		x = K.flatten(x)
# 		z_decoded = K.flatten(z_decoded)

# 		xent_loss = metrics.binary_crossentropy(x, z_decoded)
# 		kl_loss = 1 + z_log_var - K.square(z_mean) - K.exp(z_log_var)
# 		kl_loss = -kl_loss_coeff*K.mean(kl_loss,axis=-1)

# 		return K.mean(xent_loss + kl_loss)

# 	def call(self, inputs):
# 		# We have to implement custom layers by writing a call method
# 		input_data, z_decoded, z_mean, z_log_var = inputs
# 		loss = self.vae_loss(input_data, z_decoded, z_mean, z_log_var)
# 		self.add_loss(loss, inputs = inputs)

# 		# you don't use this output; but the layer must return something
# 		return input_data


def debug_message(message):
    print('[DEBUG] {}'.format(message))


def info_message(message):
    print('[INFO] {}'.format(message))


def Conv1DTranspose(filters, ksize, strides=1, pool_size=1, padding='same',
                    l1_coeff=0, l2_coeff=0, activation='relu', name=None):
    '''
    if pool_size < 2:
        pool_size == 1
    else:
        pool_size = pool_size // 2
    '''
    conv1D = Sequential(name=name)
    conv1D.add(UpSampling1D(size=strides))  # * pool_size

    if l1_coeff > 0 or l2_coeff > 0:
        kernel_regularizer = l1_l2(l1=l1_coeff, l2=l2_coeff)
    else:
        kernel_regularizer = None

    # FINDME maybe strides should be == 1 here? Check size ratios after decoder
    conv1D.add(Conv1D(filters, (ksize,),
                      padding=padding,
                      activation=activation))
    # kernel_regularizer=kernel_regularizer))

    return conv1D


def vae_sampling(args, latent_dim, batch_size=128,
                 mean=0.0, stddev=1.0, channels=None):
    latent_mean, latent_log_var = args

    # if channels is not None:
    # 	batch_shape = (batch_size, channels, latent_dim)
    # else:
    batch_shape = (batch_size, latent_dim)  # , channels

    eps = K.random_normal(shape=batch_shape, mean=mean, stddev=stddev)

    return latent_mean + K.exp(latent_log_var / 2) * eps


def dnn_sampling(args, latent_dim, batch_size=128, channels=None,
                 mean=0.0, stddev=1.0,  name='dnn_norm'):

    output = vae_sampling(args, latent_dim, batch_size=batch_size,
                          mean=mean, stddev=stddev, channels=channels)

    current_shape = K.int_shape(output)
    return Reshape(current_shape[1:] + (1,))(output)

# def dnn_sampling(args, latent_dim, batch_size=128, channels=None,
#                  mean=0.0, stddev=1.0,  name='dnn_norm'):
    # latent_mean, latent_log_var = args

    # # if channels is not None:
    # # 	batch_shape = (batch_size, channels, latent_dim)
    # # else:
    # batch_shape = (batch_size, latent_dim)  # , channels

    # eps = K.random_normal(shape=batch_shape, mean=mean, stddev=stddev)

    # gamma_ = K.exp(latent_log_var / 2) * eps

    # mean_norm = latent_mean + gamma_

    # # need to add 0's so we can sum it all to 1
    # padding = K.tf.zeros(batch_size, 1)[:, None]

    # mean_norm = concatenate([mean_norm, padding], name=name)

    # sum_exp_dnn_norm = K.sum(K.exp(mean_norm), axis=-1)[:, None]

    # output = K.exp(mean_norm) / sum_exp_dnn_norm
    # current_shape = K.int_shape(output)
    # return Reshape(current_shape[1:] + (1,))(output)


class ConvVAEPredictor(object):

    def __init__(self, encoder_filters, encoder_kernel_sizes,
                 vae_hidden_dims, dnn_hidden_dims,
                 decoder_filters, decoder_kernel_sizes,
                 encoder_pool_sizes, dnn_pool_sizes, decoder_pool_sizes,
                 dnn_filters, dnn_kernel_sizes, dnn_strides=2,
                 encoder_strides=2, decoder_strides=2,
                 vae_latent_dim=2, encoder_top_size=16,
                 final_kernel_size=3, data_shape=None,
                 batch_size=128, run_all=False,
                 verbose=False, plot_model=False,
                 original_dim=None, dnn_out_dim=None,
                 dnn_latent_dim=None, n_channels=1, dropout_rate=0.25,
                 dnn_log_var_prior=0.0, optimizer='adam-wn',
                 layer_type='Conv1D'):

        K.clear_session()
        assert(data_shape is not None)
        self.n_channels = n_channels
        self.data_shape = data_shape
        self.batch_size = batch_size
        self.vae_latent_dim = vae_latent_dim
        self.verbose = verbose
        self.dropout_rate = 0.25
        self.l1_coeff = 0
        self.l2_coeff = 0

        self.encoder_filters = encoder_filters
        self.encoder_kernel_sizes = encoder_kernel_sizes
        self.encoder_pool_sizes = encoder_pool_sizes
        self.encoder_strides = encoder_strides
        self.encoder_top_size = encoder_top_size

        self.decoder_filters = decoder_filters
        self.decoder_kernel_sizes = decoder_kernel_sizes
        self.decoder_pool_sizes = decoder_pool_sizes
        self.decoder_strides = decoder_strides
        self.final_kernel_size = final_kernel_size

        self.dnn_filters = dnn_filters
        self.dnn_kernel_sizes = dnn_kernel_sizes
        self.dnn_pool_sizes = dnn_pool_sizes
        self.dnn_strides = dnn_strides
        self.dnn_out_dim = dnn_out_dim

        self.layer_type = layer_type
        self.original_dim = original_dim

        self.vae_hidden_dims = vae_hidden_dims
        self.dnn_hidden_dims = dnn_hidden_dims
        self.vae_latent_dim = vae_latent_dim

        """FINDME: Why is this dnn_out_dim-1(??)"""
        self.dnn_latent_dim = self.data_shape[0]
        # if dnn_latent_dim is not None:
        #     self.dnn_latent_dim = dnn_out_dim - 1

        self.dnn_log_var_prior = dnn_log_var_prior
        self.optimizer = optimizer
        self.batch_size = batch_size

    def build_predictor(self, padding='same', activation='relu',
                        base_name='dnn_conv1d_{}'):

        if self.verbose:
            info_message('Building Predictor')

        x = self.input_layer

        zipper = zip(self.dnn_filters,
                     self.dnn_kernel_sizes,
                     self.dnn_pool_sizes,
                     self.dnn_strides)

        for kb, (cfilter, ksize, pool_size, stride) in enumerate(zipper):
            name = base_name.format(kb)
            x = Conv1D(cfilter, (ksize,),
                       strides=stride,
                       padding=padding,
                       activation=activation,
                       kernel_regularizer=l1_l2(l1=self.l1_coeff,
                                                l2=self.l2_coeff),
                       name=name)(x)

            x = BatchNormalization()(x)

            if False and pool_size > 0:
                # Don't bothing MaxPooling if the pool_size is 1
                x = MaxPooling1D((pool_size,))(x)

        x = Flatten()(x)

        for layer_size in self.dnn_hidden_dims:
            x = Dense(units=layer_size, activation='relu')(x)
            x = Dropout(self.dropout_rate)(x)

        # The input image ends up being encoded into these two parameters
        self.dnn_latent_mean = Dense(units=self.dnn_latent_dim,
                                     name='dnn_latent_mean')(x)
        self.dnn_latent_log_var = Dense(units=self.dnn_latent_dim,
                                        name='dnn_latent_log_var')(x)

        # Draws a latent point using a small random epsilon
        dnn_latent_layer = Lambda(dnn_sampling, name='dnn_latent_layer',
                                  arguments={'latent_dim': self.dnn_latent_dim,
                                             'batch_size': self.batch_size,
                                             'channels': self.n_channels})

        self.dnn_latent_layer = dnn_latent_layer(
            [self.dnn_latent_mean, self.dnn_latent_log_var])

        self.dnn_latent_args = concatenate([self.dnn_latent_mean,
                                            self.dnn_latent_log_var],
                                           axis=-1,
                                           name='pre_dnn_latent_args')

        shape_dnn_latent_args = K.int_shape(self.dnn_latent_args)[1:] + (1,)
        reshape_dnn_latent_args = Reshape(shape_dnn_latent_args,
                                          name='dnn_latent_args')

        self.dnn_latent_args = reshape_dnn_latent_args(self.dnn_latent_args)

        dnn_latent_mod = Lambda(lambda x: x + 1e-10, name='dnn_latent_mod')

        self.dnn_latent_mod = dnn_latent_mod(self.dnn_latent_layer)

    def build_latent_encoder(self, padding='same', activation='relu',
                             base_name='enc_conv1d_{}'):

        if self.verbose:
            info_message('Building Encoder')

        x = self.input_layer  # x = self.input_w_pred

        zipper = zip(self.encoder_filters,
                     self.encoder_kernel_sizes,
                     self.encoder_pool_sizes,
                     self.encoder_strides)

        for kb, (cfilter, ksize, pool_size, stride) in enumerate(zipper):
            name = base_name.format(kb)

            x = Conv1D(cfilter, (ksize,),
                       strides=stride,
                       padding=padding,
                       activation=activation,
                       kernel_regularizer=l1_l2(l1=self.l1_coeff,
                                                l2=self.l2_coeff),
                       name=name)(x)

            x = BatchNormalization()(x)

            if False and pool_size > 1:
                x = MaxPooling1D((pool_size,))(x)

        self.last_conv_shape = K.int_shape(x)

        x = Flatten()(x)

        for layer_size in self.vae_hidden_dims:
            x = Dense(units=layer_size, activation='relu')(x)
            x = Dropout(self.dropout_rate)(x)

        # The input image ends up being encoded into these two parameters
        self.vae_latent_mean = Dense(units=self.vae_latent_dim,
                                     name='vae_latent_mean')(x)
        self.vae_latent_log_var = Dense(units=self.vae_latent_dim,
                                        name='vae_latent_log_var')(x)

        # Draws a latent point using a small random epsilon
        vae_latent_layer = Lambda(vae_sampling, name='vae_latent_sampling',
                                  arguments={'latent_dim': self.vae_latent_dim,
                                             'batch_size': self.batch_size,
                                             'channels': self.n_channels})

        self.vae_latent_layer = vae_latent_layer([self.vae_latent_mean,
                                                  self.vae_latent_log_var])

        self.vae_latent_args = concatenate([self.vae_latent_mean,
                                            self.vae_latent_log_var],
                                           axis=-1,
                                           name='pre_vae_latent_args')

        shape_vae_latent_args = K.int_shape(self.vae_latent_args)[1:] + (1,)
        reshape_vae_latent_args = Reshape(shape_vae_latent_args,
                                          name='vae_latent_args')
        self.vae_latent_args = reshape_vae_latent_args(self.vae_latent_args)

    def build_latent_decoder(self, padding='same', activation='relu',
                             base_name='dec_conv1dT_{}'):

        if self.verbose:
            info_message('Building Decoder')
        # Input where you'll feed z
        # z_shape = K.int_shape(self.vae_latent_layer)[1:]
        # decoder_input = Input(shape = (self.vae_latent_dim,),
        # 						name = 'dec_input')
        shape_dnn_w_latent = K.int_shape(self.dnn_w_latent)[1:-1]
        reshaped_dnn_w_latent = Reshape(shape_dnn_w_latent)(self.dnn_w_latent)

        # Upsamples the input
        n_channels = self.n_channels
        divisor = int(np.prod(self.encoder_strides))
        numerator = self.data_shape[0]

        shouldbe_last_conv_shape = (numerator // divisor, n_channels)

        x = reshaped_dnn_w_latent
        for layer_size in self.vae_hidden_dims:
            x = Dense(units=layer_size, activation='relu')(x)
            x = Dropout(self.dropout_rate)(x)

        x = Dense(units=np.prod(shouldbe_last_conv_shape),
                  activation='relu')(x)
        x = Dropout(self.dropout_rate)(x)

        # Reshapes z into a feature map of the same shape as the feature map
        #	just before the last Flatten layer in the encoder model
        x = Reshape(shouldbe_last_conv_shape)(x)

        ''' Uses a Conv1DTranspose layer and a Conv1D layer to decode z into
				a feature map that is the same size as the original image input
		    '''
        zipper = zip(self.decoder_filters,
                     self.decoder_kernel_sizes,
                     self.decoder_pool_sizes,
                     self.decoder_strides)

        for kb, (cfilter, ksize, pool_size, stride) in enumerate(zipper):
            name = base_name.format(kb)

            x = Conv1DTranspose(cfilter, ksize,
                                strides=stride,
                                pool_size=pool_size,
                                padding=padding,
                                activation=activation,
                                l1_coeff=self.l1_coeff,
                                l2_coeff=self.l2_coeff,
                                name=name)(x)

            x = BatchNormalization()(x)

        self.vae_reconstruction = Conv1DTranspose(1,
                                                  self.final_kernel_size,
                                                  padding='same',
                                                  activation='linear',
                                                  # l1_coeff=self.l1_coeff,
                                                  # l2_coeff=self.l2_coeff,
                                                  name='vae_reconstruction')(x)

    # REGRESSION ONLY!
    # def dnn_kl_loss(self, labels, preds):
    #     vs = 1 - self.dnn_log_var_prior + self.dnn_latent_log_var
    #     vs = vs - K.exp(self.dnn_latent_log_var) / \
    #         K.exp(self.dnn_log_var_prior)
    #     vs = vs - K.square(self.dnn_latent_mean) / \
    #         K.exp(self.dnn_log_var_prior)

    #     return -0.5 * K.sum(vs, axis=-1)
    def dnn_kl_loss(self, labels, preds):
        Z_mean = self.dnn_latent_args[:, :self.dnn_latent_dim]
        Z_log_var = self.dnn_latent_args[:, self.dnn_latent_dim:]
        k_summer = 1 + Z_log_var - K.square(Z_mean) - K.exp(Z_log_var)
        return -0.5 * K.sum(k_summer, axis=-1)

    def dnn_predictor_loss(self, y_true, y_pred):
        reconstruction_loss = mean_squared_error(y_true, y_pred)
        return reconstruction_loss

    def build_model(self, batch_size=None, hidden_activation='relu',
                    output_activation='sigmoid',
                    dnn_weight=1.0, vae_weight=1.0, vae_kl_weight=1.0,
                    dnn_kl_weight=1.0, optimizer='adam', metrics=None):

        batch_shape = (self.batch_size, self.original_dim, 1)
        self.input_layer = Input(batch_shape=batch_shape, name='input_layer')

        self.build_predictor()

        # self.input_w_pred = concatenate(
        #     [self.input_layer, self.dnn_latent_layer],
        #     axis=-2, name='data_input_w_dnn_latent_out')

        self.build_latent_encoder()

        vae_latent_shape = K.int_shape(self.vae_latent_layer)[1:] + (1,)

        self.vae_latent_layer = Reshape(
            vae_latent_shape)(self.vae_latent_layer)

        self.dnn_w_latent = concatenate(
            [self.dnn_latent_layer, self.vae_latent_layer],
            axis=-2, name='dnn_latent_out_w_prev_w_vae_lat')

        self.build_latent_decoder()

        output_stack = {'vae_reconstruction': self.vae_reconstruction,
                        'dnn_latent_layer': self.dnn_latent_layer,
                        'dnn_latent_mod': self.dnn_latent_mod,
                        'vae_latent_args': self.vae_latent_args}

        output_stack = [self.vae_reconstruction, self.dnn_latent_layer,
                        self.dnn_latent_mod, self.vae_latent_args]

        self.model = Model([self.input_layer], output_stack)

        # def compile(self, dnn_weight = 1.0, vae_weight = 1.0, vae_kl_weight = 1.0,
        # 			  dnn_kl_weight = 1.0, optimizer = 'adam', metrics = None):
        metrics_ = {'dnn_prediction': ['mse'],
                    'dnn_latent_layer': ['mse'],
                    'vae_reconstruction': ['mse']}

        self.model.compile(
            optimizer=optimizer or self.optimizer,

            loss={'vae_reconstruction': self.vae_reconstruction_loss,
                  'dnn_latent_layer': self.vae_kl_loss,  # Regression: Norm
                  'dnn_latent_mod': self.dnn_predictor_loss,
                  'vae_latent_args': self.vae_kl_loss},

            loss_weights={'vae_reconstruction': vae_weight,
                          'dnn_latent_layer': dnn_kl_weight,
                          'dnn_latent_mod': dnn_weight,
                          'vae_latent_args': vae_kl_weight},

            metrics=metrics or metrics_
        )

    def vae_reconstruction_loss(self, input_layer, vae_reconstruction):
        # reshape_input_layer = Reshape((self.batch_size, self.data_shape[0]))
        # input_layer = reshape_input_layer(input_layer)

        inp_vae_loss = mean_squared_error(input_layer, vae_reconstruction)

        return inp_vae_loss

    def vae_kl_loss(self, ztrue, zpred):
        Z_mean = self.vae_latent_args[:, :self.vae_latent_dim]
        Z_log_var = self.vae_latent_args[:, self.vae_latent_dim:]
        k_summer = 1 + Z_log_var - K.square(Z_mean) - K.exp(Z_log_var)
        return -0.5 * K.sum(k_summer, axis=-1)

    # REGRESSION ONLY!
    # def dnn_kl_loss(self, labels, preds):
    #     vs = 1 - self.dnn_log_var_prior + self.dnn_latent_log_var

    #     vs = vs - K.exp(self.dnn_latent_log_var) / \
    #         K.exp(self.dnn_log_var_prior)

    #     vs = vs - K.square(self.dnn_latent_mean) / \
    #         K.exp(self.dnn_log_var_prior)

    #     return -0.5 * K.sum(vs, axis=-1)

    def load_model(self, model_file):
        ''' there's a currently bug in the way keras loads models 
                        from `.yaml` files that has to do with `Lambda` calls
                        ... so this is a hack for now
        '''
        self.build_model()
        self.model.load_weights(model_file)

    def save(self):
        joblib_save_loc = '{}/{}_{}_{}_trained_model_output_{}.joblib.save'
        joblib_save_loc = joblib_save_loc.format(self.model_dir, self.run_name,
                                                 self.generationID, self.chromosomeID,
                                                 self.time_stamp)

        wghts_save_loc = '{}/{}_{}_{}_trained_model_weights_{}.save'
        wghts_save_loc = wghts_save_loc.format(self.model_dir, self.run_name,
                                               self.generationID, self.chromosomeID,
                                               self.time_stamp)

        model_save_loc = '{}/{}_{}_{}_trained_model_full_{}.save'
        model_save_loc = model_save_loc.format(self.model_dir, self.run_name,
                                               self.generationID, self.chromosomeID,
                                               self.time_stamp)

        self.neural_net.save_weights(wghts_save_loc, overwrite=True)
        self.neural_net.save(model_save_loc, overwrite=True)

        try:
            joblib.dump({'best_loss': self.best_loss, 'history': self.history},
                        joblib_save_loc)
        except Exception as e:
            print(str(e))
