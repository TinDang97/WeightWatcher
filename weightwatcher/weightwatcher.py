# Copyright 2018 Calculation Consulting [calculationconsulting.com]
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys, os 
import logging

import numpy as np
import pandas as pd

import matplotlib
import matplotlib.pyplot as plt
import powerlaw
 
 
from sklearn.decomposition import TruncatedSVD

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.models import load_model

import torch
import torch.nn as nn


#from .RMT_Util import *
#from .constants import *

from .RMT_Util import *
from .constants import *

# TODO:  allow configuring custom logging
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('WeightWatcher')


MAX_NUM_EVALS= 1000

def main():
    """
    Weight Watcher
    """
    print("WeightWatcher command line support coming later. https://calculationconsulting.com")




class WWLayer:
    """WW wrapper layer to Keras and PyTorch Layer layer objects
       Uses pythong metaprogramming to add result columns for the final details dataframe"""
       
    def __init__(self, layer, index=-1, name="", 
                 the_type=LAYER_TYPE.UNKNOWN, framework=FRAMEWORK.UNKNOWN, skipped=False):
        self.layer = layer
        self.index = index
        self.name = name
        self.skipped = skipped
        self.the_type = the_type
        self.framework = framework
        
        self.channels = CHANNELS.UNKNOWN

        if (self.framework==FRAMEWORK.KERAS):
            self.channels = CHANNELS.FIRST
        elif (self.framework==FRAMEWORK.PYTORCH):
            self.channels = CHANNELS.LAST
        
        # original weights (tensor) and biases
        self.has_weights = False
        self.weights = None
  
        # extracted weight matrices
        self.num_W = 0
        self.Wmats = []
        self.N = 0
        self.M = 0
        self.num_components = self.M # default for full SVD, not used yet
        self.rf = 1 #receptive field size, default for dense layer
        self.inputs_shape = []
        self.outputs_shape = []
        
        # evals 
        self.evals = None
        
        # details, set by metaprogramming in apply_xxx() methods
        self.columns = []
        
        
    def add_column(self, name, value):
        """Add column to the details dataframe"""
        self.columns.append(name)
        self.__dict__[name] =  value
        
    def get_row(self, params={}):
        """get a details dataframe row from the columns and metadata"""
        data = {}
        
        data['layer_id']=self.index
        data['name']=self.name
        data['N']=self.N
        data['M']=self.M
        data['rf']=self.rf
        
        for col in self.columns:
            data[col]=self.__dict__[col]
        
        return data
    
    
    
    def layer_type(self, layer):
        """Given a framework layer, determine the weightwatcher LAYER_TYPE
        This can detect basic Keras and PyTorch classes by type, and will try to infer the type otherwise. """

        the_type = LAYER_TYPE.UNKNOWN
       
        typestr = (str(type(layer))).lower()
            
        # Keras TF 2.x types
        if isinstance(layer, keras.layers.Dense): 
            the_type = LAYER_TYPE.DENSE
            
        elif isinstance(layer, keras.layers.Conv1D):                
            the_type = LAYER_TYPE.CONV1D
        
        elif isinstance(layer, keras.layers.Conv2D):                
            the_type = LAYER_TYPE.CONV2D
            
        elif isinstance(layer, keras.layers.Flatten):
            the_type = LAYER_TYPE.FLATTENED
            
        elif isinstance(layer, keras.layers.Embedding):
            the_type = LAYER_TYPE.EMBEDDING
            
        elif isinstance(layer, tf.keras.layers.LayerNormalization):
            the_type = LAYER_TYPE.NORM
                 
        
        # PyTorch        
             
        elif isinstance(layer, nn.Linear):
            the_type = LAYER_TYPE.DENSE
            
        elif isinstance(layer, nn.Conv1d):
            the_type = LAYER_TYPE.CONV1D
        
        elif isinstance(layer, nn.Conv2d):
            the_type = LAYER_TYPE.CONV2D
            
        elif isinstance(layer, nn.Embedding):
            the_type = LAYER_TYPE.EMBEDDING
                
        elif isinstance(layer, nn.LayerNorm):
            the_type = LAYER_TYPE.NORM
            
        
        # try to infer type (i.e for huggingface)
        elif typestr.endswith(".linear'>"):
            the_type = LAYER_TYPE.DENSE
            
        elif typestr.endswith(".dense'>"):
            the_type = LAYER_TYPE.DENSE
            
        elif typestr.endswith(".conv1d'>"):
            the_type = LAYER_TYPE.CONV1D
            
        elif typestr.endswith(".conv2d'>"):
            the_type = LAYER_TYPE.CONV2D

        
        return the_type


    
    def make(self, filter_ids=None, filter_types=None):
        """ Constructor for WWLayer class.  Make a ww (wrapper)_layer from a framework layer, or return None if layer is skipped.
        In particular , late uses specify filter on layer ids and names """
        
        self.the_type = self.layer_type(self.layer)
        has_weights = False;
        
        if hasattr(self.layer, 'name'):
            name = self.layer.name
        
        if filter_ids is not None and len(filter_ids) > 0:
            if self.layer_id not in filter_ids:
                self.skipped = True
                
        if filter_types is not None and len(filter_types) > 0:
            if self.the_type not in filter_types:
                self.skipped = True
        
        # TODO: maybe move this to the constructor
        if not self.skipped:
            has_weights, weights, has_biases, biases = self.get_weights_and_biases()
            
            self.has_weights = has_weights
            self.has_biases = has_biases
            
            if has_biases:
                self.biases = biases   
                
            if has_weights:    
                self.weights = weights
                self.set_weight_matrices(weights)
    
        return self
    


        
    def get_weights_and_biases(self):
        """extract the original weights (as a tensor) for the layer, and biases for the layer, if present"""
        
        has_weights, has_biases = False, False
        weights, biases = None, None
    
        if self.framework==FRAMEWORK.PYTORCH:
            if hasattr(self.layer, 'weight'):
                w = [np.array(self.layer.weight.data.clone().cpu())]
                has_weights = True
                
        elif self.framework==FRAMEWORK.KERAS:
            w = self.layer.get_weights()
            if(len(w)>0):
                has_weights = True
                
            if(len(w)>1):
                has_biases = True
                
        else:
            logger.error("unknown framework: weighwatcher only supports keras (tf 2.x) or pytorch ")
       
        if has_weights:
            if len(w)==1:
                weights = w[0]
                biases = None
            elif len(w)==2:
                weights = w[0]
                biases =  w[1]
            else:
                logger.error("unknown weights, with len(w)={} ".format(len(w)))
        
        return has_weights, weights, has_biases, biases  
    
    
      
    def set_weight_matrices(self, weights, conv2d_fft=False, conv2d_norm=True):
        """extract the weight matrices from the framework layer weights (tensors)
        sets the weights and detailed properties on the ww (wrapper) layer 
    
        conv2d_fft not supported yet """
   
        if not self.has_weights:
            logger.info("Layer {} {} has no weights".format(self.index, self.name))
            return 
        
        the_type = self.the_type
        
        N, M, n_comp, rf = 0, 0, 0, None
        Wmats = []
        
        # this may change if we treat Conv1D differently layer
        if (the_type == LAYER_TYPE.DENSE or the_type == LAYER_TYPE.CONV1D):
            Wmats = self.weights
            N, M = np.max(Wmats.shape), np.min(Wmats.shape)
            n_comp = M
            rf = 1
            
        # TODO: reset channels nere ?    
        elif the_type == LAYER_TYPE.CONV2D:
            Wmats, N, M, rf, channels = self.conv2D_Wmats(weights)
            n_comp = M
            self.channels = channels

            
        elif the_type == LAYER_TYPE.NORM:
            logger.info("Layer id {}  Layer norm has no matrices".format(self.index))
        
        else:
            logger.info("Layer id {}  unknown type {} layer  {}".format(self.index, the_type, self.layer))
    
        self.N = N
        self.M = M
        self.rf = rf
        self.Wmats = Wmats
        self.num_components = n_comp
        
        return 
    

            
        
    def __repr__(self):
        return "WWLayer()"

    def __str__(self):
        return "WWLayer {}  {} {} {}  skipped {}".format(self.index, self.name, 
                                                       self.framework.name, self.the_type.name, self.skipped)
        
            
      
    
    def conv2D_Wmats(self, Wtensor, channels=CHANNELS.UNKNOWN):
        """Extract W slices from a 4 index conv2D tensor of shape: (N,M,i,j) or (M,N,i,j).  
        Return ij (N x M) matrices, with receptive field size (rf) and channels flag (first or last)"""
        
        logger.debug("conv2D_Wmats")
        
        # TODO:  detect or use channels
        # if channels specified ...
    
        Wmats = []
        s = Wtensor.shape
        N, M, imax, jmax = s[0],s[1],s[2],s[3]
        if N + M >= imax + jmax:
            logger.debug("Channels Last tensor shape detected: {}x{} (NxM), {}x{} (i,j)".format(N, M, imax, jmax))
            
            channels=CHANNELS.LAST
            for i in range(imax):
                for j in range(jmax):
                    W = Wtensor[:,:,i,j]
                    if N < M:
                        W = W.T
                    Wmats.append(W)
        else:
            N, M, imax, jmax = imax, jmax, N, M          
            logger.debug("Channels First shape detected: {}x{} (NxM), {}x{} (i,j)".format(N, M, imax, jmax))
            
            channels=CHANNELS.FIRST
            for i in range(imax):
                for j in range(jmax):
                    W = Wtensor[i,j,:,:]
                    if N < M:
                        W = W.T
                    Wmats.append(W)
                    
        rf = imax*jmax # receptive field size             
        logger.debug("get_conv2D_Wmats N={} M={} rf= {} channels = {}".format(N,M,rf,channels))
    
        return Wmats, N, M, rf, channels    

    
  
      



    
class WWLayerIterator:
    """Iterator that loops over ww wrapper layers, with original matrices (tensors) and biases (optional) available."""

    def __init__(self, model, filter_ids=[], filter_types=[]):
        self.model = model
        self.layers_iter, self.framework = self.model_iter(model) 

        self.filter_ids = filter_ids
        self.filter_types = filter_types
        
        self.k = 0
        

    def __iter__(self):
        return self
    
    # Python 3 compatibility
    def __next__(self):
        return self.next()
    
    
    def model_iter(self, model):
        """Create a python iterator for the layers in the model. Also detects the framework being used."""
        layer_iter = None
        
        if hasattr(model, 'layers'):
            layer_iter = (l for l in model.layers)
            framework = FRAMEWORK.KERAS
        elif hasattr(model, 'modules'):
            layer_iter = model.modules()
            framework = FRAMEWORK.PYTORCH
        else:
            layer_iter = None
            framework = FRAMEWORK.UNKNOWN
            
        return layer_iter, framework



    def next(self):
        curr_layer = next(self.layers_iter)
        if curr_layer:    
            curr_id, self.k = self.k, self.k+1
            
            ww_layer = WWLayer(curr_layer, index=curr_id, framework=self.framework)
            ww_layer.make(filter_ids=self.filter_ids, filter_types=self.filter_types)
                        
            return ww_layer
        else:
            raise StopIteration()


class WeightWatcher(object):

    def __init__(self, model=None, log=True):
        self.model = self.load_model(model)
        #self.setup_custom_logger(log, logger)     
        logger.info(self.banner())

#     def setup_custom_logger(self, log, logger):
#         formatter = logging.Formatter(fmt='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
#     
#         handler = logging.StreamHandler()
#         handler.setFormatter(formatter)
#     
#         if not logger:
#            logger = logging.getLogger(__name__)
#         
#         if not logger.handlers: # do not register handlers more than once
#             if log:
#                 logging.setLevel(logging.INFO) 
#                 console_handler = logging.StreamHandler()
#                 formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
#                 console_handler.setFormatter(formatter)
#                 self.logger.addHandler(console_handler)
#             else:
#                 self.logger.addHandler(logging.NullHandler())
#   
#         return logger



    def header(self):
        """WeightWatcher v0.1.dev0 by Calculation Consulting"""
#        from weightwatcher import __name__, __version__, __author__, __description__, __url__
#        return "{} v{} by {}\n{}\n{}".format(__name__, __version__, __author__, __description__, __url__)
        return ""

    def banner(self):
        versions  = "\npython      version {}".format(sys.version)
        versions += "\nnumpy       version {}".format(np.__version__)
        versions += "\ntensforflow version {}".format(tf.__version__)
        versions += "\nkeras       version {}".format(tf.keras.__version__)
        return "\n{}{}".format(self.header(), versions)


    def __repr__(self):
        done = bool(self.results)
        txt  = "\nAnalysis done: {}".format(done)
        return "{}{}".format(self.header(), txt)

            
    # TODO: get rid of this or extend to be more generally useful
    def load_model(self,model):
        """load the model from a file, only works for keras right now"""
        res = model
        if isinstance(model, str):
            if os.path.isfile(model):
                logger.info("Loading model from file '{}'".format(model))
                res = load_model(model)
            else:
                logger.error("Loading model from file '{}': file not found".format(model))
        return res
    


    
    

            
    
    # TODO: implement
    def same_models(self, model_1, model_2):
        """Compare models to see if the are the same architecture.
        Not really impelemnted yet"""
    
        same = True
        layer_iter_1 = WWLayerIterator(model_1)
        layer_iter_2 = WWLayerIterator(model_2)
        
        same = layer_iter_1.framework == layer_iter_2.framework 

        return same
    
    def distances(self, model_1, model_2):
        """Compute the distances between model_1 and model_2 for each layer. 
        Reports Frobenius norm of the distance between each layer weights (tensor)
        
           < ||W_1-W_2|| >
           
        output: avg delta W, a details dataframe
           
        models should be the same size and from the same framework
           
        """
        
        # check and throw exception if inputs incorrect
        # TODO: review design here...may need something else
        #   need to:
        #.   - iterate over all layers and check
        #.   - inspect framework by framework
        #.   - check here instead
        #
        
        same = True
        layer_iter_1 = WWLayerIterator(model_1)
        layer_iter_2 = WWLayerIterator(model_2)
        
        same = layer_iter_1.framework == layer_iter_2.framework 
        if not same:
            raise Exception("Sorry, models are from different frameworks")
            
       
        
        details = pd.DataFrame(columns = ['layer_id', 'name', 'delta_W', 'delta_b', 'W_shape', 'b_shape'])
        data = {}
        
        try:      
            for layer_1, layer_2 in zip(layer_iter_1, layer_iter_2):
                data['layer_id'] = layer_1.index
                data['name'] = layer_1.name
    
                if layer_1.has_weights:
                    data['delta_W'] = np.linalg.norm(layer_1.weights-layer_2.weights)
                    data['W_shape'] = layer_1.weights.shape
    
                    if layer_1.has_biases:
                        data['delta_b'] = np.linalg.norm(layer_1.biases-layer_2.biases)
                        data['b_shape'] = layer_1.biases.shape
    
                    details = details.append(data,  ignore_index=True)
        except:
            logger.error("Sorry, problem comparing models")
            raise Exception("Sorry, problem comparing models")
        
        details.set_index('layer_id', inplace=True)
        avg_dW = np.mean(details['delta_W'].to_numpy())
        return avg_dW, details
    
   
    
    def combined_eigenvalues(self, Wmats, N, M, n_comp, params):
        """Compute the eigenvalues for all weights of the NxM weight matrices (N >= M), 
            combined into a single, sorted, numpy array
    
            Applied normalization and glorot_fix if specified
    
            Assumes an array of weights comes from a conv2D layer and applies conv2d_norm normalization by default
    
            Also returns max singular value and rank_loss, needed for other calculations
         """
    
        all_evals = []
        max_sv = 0.0
        rank_loss = 0
    
        #TODO:  allow user to specify
        normalize = params['normalize']
        glorot_fix = params['glorot_fix']
        conv2d_norm = params['conv2d_norm']
    
    
        count = len(Wmats)
        for  W in Wmats:
    
            Q=N/M
            # not really used
            check, checkTF = self.glorot_norm_check(W, N, M, count) 
    
            # assume receptive field size is count
            if glorot_fix:
                W = self.glorot_norm_fix(W, N, M, count)
            elif conv2d_norm:
                # probably never needed since we always fix for glorot
                W = W * np.sqrt(count/2.0) 
    
            # SVD can be swapped out here
            # svd = TruncatedSVD(n_components=M-1, n_iter=7, random_state=10)
    
            W = W.astype(float)
            logger.debug("Running full SVD:  W.shape={}  n_comp = {}".format(W.shape, n_comp))
            sv = np.linalg.svd(W, compute_uv=False)
            sv = sv.flatten()
            sv = np.sort(sv)[-n_comp:]
            # TODO:  move to PL fit for robust estimator
            #if len(sv) > max_size:
            #    #logger.info("chosing {} singular values from {} ".format(max_size, len(sv)))
            #    sv = np.random.choice(sv, size=max_size)
    
            #sv = svd.singular_values_
            evals = sv*sv
            if normalize:
                evals = evals/N
    
            all_evals.extend(evals)
    
            max_sv = np.max([max_sv, np.max(sv)])
            max_ev = np.max(evals)
            rank_loss = 0#rank_loss + self.calc_rank_loss(sv, M, max_ev)
    
        return np.sort(np.array(all_evals)), max_sv, rank_loss



    
    def layer_supported(self, ww_layer, params={}):
        """Return true if this kind of layer is supported"""
        
        layer_id = ww_layer.index
        name = ww_layer.name
        the_type = ww_layer.the_type
        rf = ww_layer.rf
        
        M  = ww_layer.M
        N  = ww_layer.N
        
        min_size = params.get('min_size')
        max_size = params.get('max_size')
        
        supported = False
        if ww_layer.skipped:
            logger.debug("Layer {} {} is skipped".format(layer_id, name))
            
        elif not ww_layer.has_weights:
            logger.debug("Layer {} {} has no weights".format(layer_id, name))
        
        elif the_type is LAYER_TYPE.UNKNOWN:
            logger.debug("Layer {} {} type {} unknown".format(layer_id, name, the_type))
        
        elif the_type in [LAYER_TYPE.FLATTENED, LAYER_TYPE.NORM]:
            logger.debug("Layer {} {} type {} not supported".format(layer_id, name, the_type))
        
        elif min_size and M*rf < min_size:
            logger.debug("Layer {} {}: size {} < {}".format(layer_id, name, M*rf, min_size))
                  
        elif max_size and N > max_size:
            logger.debug("Layer {} {}: size {} > {}".format(layer_id, name, N, max_size))
        
        elif the_type  in [LAYER_TYPE.DENSE, LAYER_TYPE.CONV1D, LAYER_TYPE.CONV2D]:
            supported = True
            
        return supported
    
    
            
    def apply_esd(self, ww_layer, params={}):
        """run full SVD on layer weight matrices, compute ESD, combine all,  and save to layer """
        
        layer_id = ww_layer.index
        name = ww_layer.name
        the_type = ww_layer.the_type
         
        M  = ww_layer.M
        N  = ww_layer.N
        rf = ww_layer.rf
            
        if self.layer_supported(ww_layer, params):
        
            Wmats = ww_layer.Wmats
            n_comp = ww_layer.num_components
            
            evals, sv_max, rank_loss = self.combined_eigenvalues(Wmats, N, M, n_comp, params)
         
            ww_layer.evals = evals
            ww_layer.add_column("has_esd",True)
            ww_layer.add_column("num_evals",len(evals))
            ww_layer.add_column("sv_max",sv_max)
            ww_layer.add_column("rank_loss",rank_loss)
            ww_layer.add_column("lambda_max",np.max(evals))
            
        return ww_layer
              
           
    def apply_plot_esd(self, ww_layer, params={}):
        """Plot the ESD on regular and log scale.  Only used when powerlaw fit not called"""
        
        if self.layer_supported(ww_layer, params):
            
            evals = ww_layer.evals
            name = ww_layer.name
            
            plt.title(name)
            plt.hist(evals, bins=100)
            plt.show()
            
            plt.title(name)
            plt.hist(np.log10(evals), bins=100)
            plt.show()
            
        return ww_layer
    
 
    def apply_fit_powerlaw(self, ww_layer, params={}):
        """Plot the ESD on regular and log scale.  Only used when powerlaw fit not called"""
        
        if self.layer_supported(ww_layer, params):
            
            evals = ww_layer.evals
            layer_id = ww_layer.index
            name = ww_layer.name
            title = "{} {}".format(layer_id, name)
                                
            alpha, xmin, xmax, D, sigma = self.fit_powerlaw(evals, title=title)
            
            ww_layer.alpha = alpha
            ww_layer.xmin = xmin
            ww_layer.xmax = xmax
            ww_layer.D = D
            ww_layer.sigma = sigma

            
        return ww_layer
    
    
    
          
              

    # test with https://github.com/osmr/imgclsmob/blob/master/README.md
    def analyze(self, model=None, layers=[], min_size=3, max_size=10000,
                alphas=False, lognorms=True, spectralnorms=False, softranks=False,
                normalize=False, glorot_fix=False, plot=False, mp_fit=False, conv2d_fft=False,
                conv2d_norm=True, fit_bulk = False, params={}):
        """
        Analyze the weight matrices of a model.

        layers:
            List of layer ids. If empty, analyze all layers (default)
        min_size:
            Minimum weight matrix size to analyze
        max_size:
            Maximum weight matrix size to analyze (0 = no limit)
        normalize:
            Normalize the X matrix. Usually True for Keras, False for PyTorch
        glorot_fix:
            Adjust the norm for the Glorot Normalization
        alphas:
            Compute the power laws (alpha) of the weight matrices. 
            Time consuming so disabled by default (use lognorm if you want speed)
        lognorms:
            Compute the log norms of the weight matrices.
        spectralnorms:
            Compute the spectral norm (max eigenvalue) of the weight matrices.
        softranks:
            Compute the soft norm (i.e. StableRank) of the weight matrices.
        mp_fit:
            Compute the best Marchenko-Pastur fit of each weight matrix ESD
        conv2d_fft:
            For Conv2D layers, use FFT method.  Otherwise, extract and combine the weight matrices for each receptive field
            Note:  for conf2d_fft, the ESD is automatically subsampled to max_size eigenvalues max
        fit_bulk: 
            Attempt to fit bulk region of ESD only
        device: N/A yet
            if 'gpu'  use torch.svd()
            else 'cpu' use np.linalg.svd
        """

        model = model or self.model        
        
        params['min_size'] = min_size
        params['max_size'] = max_size
        params['plot'] = plot
        params['normalize'] = normalize
        params['glorot_fix'] = glorot_fix
        params['conv2d_norm'] = conv2d_norm
   
        layer_iterator = WWLayerIterator(model)     
        details = pd.DataFrame(columns = ['layer_id', 'name'])
           
        for ww_layer in layer_iterator:
           if(ww_layer.has_weights):
               self.apply_esd(ww_layer, params)
               print(" evals {}".format(ww_layer.evals))
               self.apply_fit_powerlaw(ww_layer, params)
               details = details.append(ww_layer.get_row(),  ignore_index=True)
        
        results = {}
        self.print_results(results=results)

        return details

    
    
    def print_results(self, results=None):
        self.compute_details(results=results)

    def get_details(self, results=None):
        """
        Return a pandas dataframe with details for each layer
        """
        df = self.compute_details(results=results)
        details =  df[:-1].dropna(axis=1, how='all').set_index("layer_id") # prune the last line summary
        return details[details.layer_type.notna()]

    def compute_details(self, results=None):
        """
        Return a pandas dataframe with details for each layer
        """
        import numpy as np
        
        if results is None:
            results = self.results

        if not results:
            logger.warn("No results to print")
            return

        logger.info("### Printing results ###")

        # not all implemented for detais, many are jsut for debugging
        metrics = {
            # key in "results" : pretty print name
            "D": "D",
            "sigma": "sigma",
            "D2": "D2",
            "norm": "Norm",
            "lognorm": "LogNorm",
            "alpha": "Alpha",
            "alpha2": "Alpha2",
            "alpha_weighted": "Alpha Weighted",
            "alpha2_weighted": "Alpha2 Weighted",
            "spectralnorm": "Spectral Norm",
            "logspectralnorm": "Log Spectral Norm",
            "softrank": "Softrank",
            "softranklog": "Softrank Log",
            "softranklogratio": "Softrank Log Ratio",
            "sigma_mp": "Marchenko-Pastur (MP) fit sigma",
            "numofSpikes": "Number of spikes per MP fit",
            "ratio_numofSpikes": "aka, percent_mass, Number of spikes / total number of evals",
            "softrank_mp": "Softrank for MP fit",
            "logpnorm": "alpha pNorm",
            "xmin": "xin of PL fit",
            "xmax": "xmax of PL fit",
            "rand_xmax": "xmax of random X "



        }

        metrics_stats = []
        for metric in metrics:
            metrics_stats.append("{}_min".format(metric))
            metrics_stats.append("{}_max".format(metric))
            metrics_stats.append("{}_avg".format(metric))

            metrics_stats.append("{}_compound_min".format(metric))
            metrics_stats.append("{}_compound_max".format(metric))
            metrics_stats.append("{}_compound_avg".format(metric))

        columns = ["layer_id", "layer_type", "N", "M", "layer_count", "slice", 
                   "slice_count", "level", "comment"] + [*metrics] + metrics_stats
        df = pd.DataFrame(columns=columns)

        metrics_values = {}
        metrics_values_compound = {}

        for metric in metrics:
            metrics_values[metric] = []
            metrics_values_compound[metric] = []

        layer_count = 0
        for layer_id, result in results.items():
            layer_count += 1

            layer_type = np.NAN
            if "layer_type" in result:
                layer_type = str(result["layer_type"]).replace("LAYER_TYPE.", "")

            compounds = {} # temp var
            for metric in metrics:
                compounds[metric] = []

            slice_count = 0
            Ntotal = 0
            Mtotal = 0
            for slice_id, summary in result.items():
                if not str(slice_id).isdigit():
                    continue

                slice_count += 1

                N = np.NAN
                if "N" in summary:
                    N = summary["N"]
                    Ntotal += N

                M = np.NAN
                if "M" in summary:
                    M = summary["M"]
                    Mtotal += M

                data = {"layer_id": layer_id, "layer_type": layer_type, "N": N, "M": M, "slice": slice_id,  "comment": "Slice level"}
                for metric in metrics:
                    if metric in summary:
                        value = summary[metric]
                        if value is not None:
                            metrics_values[metric].append(value)
                            compounds[metric].append(value)
                            data[metric] = value
                row = pd.DataFrame(columns=columns, data=data, index=[0])
                df = pd.concat([df, row])

            data = {"layer_id": layer_id, "layer_type": layer_type, "N": Ntotal, "M": Mtotal, "slice_count": slice_count, "comment": "Layer level"}
            # Compute the coumpound value over the slices
            for metric, value in compounds.items():
                count = len(value)
                if count == 0:
                    continue

                compound = np.mean(value)
                metrics_values_compound[metric].append(compound)
                data[metric] = compound

                if count > 1:
                    # Compound value of the multiple slices (conv2D)
                    logger.debug("Layer {}: {} compound: {}".format(layer_id, metrics[metric], compound))
                else:
                    # No slices (Dense or Conv1D)
                    logger.debug("Layer {}: {}: {}".format(layer_id, metrics[metric], compound))

            row = pd.DataFrame(columns=columns, data=data, index=[0])
            df = pd.concat([df, row])

        data = {"layer_count": layer_count, "comment": "Network Level"}
        for metric, metric_name in metrics.items():
            if metric not in metrics_values or len(metrics_values[metric]) == 0:
                continue

            values = metrics_values[metric]
            minimum = min(values)
            maximum = max(values)
            avg = np.mean(values)
            self.summary[metric] = avg
            logger.info("{}: min: {}, max: {}, avg: {}".format(metric_name, minimum, maximum, avg))
            data["{}_min".format(metric)] = minimum
            data["{}_max".format(metric)] = maximum
            data["{}_avg".format(metric)] = avg

            values = metrics_values_compound[metric]
            minimum = min(values)
            maximum = max(values)
            avg = np.mean(values)
            self.summary["{}_compound".format(metric)] = avg
            logger.info("{} compound: min: {}, max: {}, avg: {}".format(metric_name, minimum, maximum, avg))
            data["{}_compound_min".format(metric)] = minimum
            data["{}_compound_max".format(metric)] = maximum
            data["{}_compound_avg".format(metric)] = avg

        row = pd.DataFrame(columns=columns, data=data, index=[0])
        df = pd.concat([df, row])
        df['slice'] += 1 #fix the issue that slice starts from 0 and don't match the plot

        return df.dropna(axis=1,how='all')

    
    def get_summary(self, pandas=False):
        if pandas:
            return pd.DataFrame(data=self.summary, index=[0])
        else:
            return self.summary


    
    def get_conv2D_fft(self, W, n=32):
        """Compute FFT of Conv2D channels, to apply SVD later"""
        
        logger.info("get_conv2D_fft on W {}".format(W.shape))

        # is pytorch or tensor style 
        s = W.shape
        logger.debug("    Conv2D SVD ({}): Analyzing ...".format(s))

        N, M, imax, jmax = s[0],s[1],s[2],s[3]
        # probably better just to check what col N is in 
        if N + M >= imax + jmax:
            logger.debug("[2,3] tensor shape detected: {}x{} (NxM), {}x{} (i,j)".format(N, M, imax, jmax))    
            fft_axes = [2,3]
        else:
            N, M, imax, jmax = imax, jmax, N, M          
            fft_axes = [0,1]
            logger.debug("[1,2] tensor shape detected: {}x{} (NxM), {}x{} (i,j)".format(N, M, imax, jmax))

        # Switch N, M if in wrong order
        if N < M:
            M, N = N, M

        #  receptive_field / kernel size
        rf = np.min([imax, jmax])
        # aspect ratio
        Q = N/M 
        # num non-zero eigenvalues  rf is receptive field size (sorry calculated again here)
        n_comp = rf*N*M
        
        logger.info("N={} M={} n_comp {} ".format(N,M,n_comp))

        # run FFT on each channel
        fft_grid = [n,n]
        fft_coefs = np.fft.fft2(W, fft_grid, axes=fft_axes)
        
        return [fft_coefs], N, M, n_comp



    def normalize_evals(self, evals, N, M):
        """DEPRECATED: Normalize the eigenvalues W by N and receptive field size (if needed)"""
        logger.debug(" normalzing evals, N, M {},{},{}".format(N,M))
        return evals/np.sqrt(N)

    def glorot_norm_fix(self, W, N, M, rf_size):
        """Apply Glorot Normalization Fix"""

        kappa = np.sqrt( 2 / ((N + M)*rf_size) )
        W = W/kappa
        return W 

    def pytorch_norm_fix(self, W, N, M, rf_size):
        """Apply pytorch Channel Normalization Fix

        see: https://chsasank.github.io/vision/_modules/torchvision/models/vgg.html
        """

        kappa = np.sqrt( 2/(N*rf_size) )
        W = W/kappa
        return W 


    def glorot_norm_check(self, W, N, M, rf_size, 
                   lower = 0.5, upper = 1.5):
        """Check if this layer needs Glorot Normalization Fix"""

        kappa = np.sqrt( 2 / ((N + M)*rf_size) )
        norm = np.linalg.norm(W)

        check1 = norm / np.sqrt(N*M)
        check2 = norm / (kappa*np.sqrt(N*M))
        
        if (rf_size > 1) and (check2 > lower) and (check2 < upper):   
            return check2, True
        elif (check1 > lower) & (check1 < upper): 
            return check1, True
        else:
            if rf_size > 1:
                return check2, False
            else:
                return check1, False
            
    
    
    
    def random_eigenvalues(self, Wmats, n_comp, num_replicas=1, min_size=1, max_size=10000, 
                           normalize=True, glorot_fix=False, conv2d_norm=True):
        """Compute the eigenvalues for all weights of the NxM skipping layer, num evals ized weight matrices (N >= M), 
            combined into a single, sorted, numpy array
    
        see: combined_eigenvalues()
        
         """
         
         
        all_evals = []

        logger.info("generating {} replicas for each W of the random eigenvalues".format(num_replicas))
        for num in range(num_replicas):
            count = len(Wmats)
            for  W in Wmats:
    
                M, N = np.min(W.shape), np.max(W.shape)
                if M >= min_size:# and M <= max_size:
    
                    Q=N/M
                    check, checkTF = self.glorot_norm_check(W, N, M, count) 
        
                    # assume receptive field size is count
                    if glorot_fix:
                        W = self.glorot_norm_fix(W, N, M, count)
                    elif conv2d_norm:
                        # probably never needed since we always fix for glorot
                        W = W * np.sqrt(count/2.0) 
                    
                    Wrand = W.flatten()
                    np.random.shuffle(Wrand)
                    W = Wrand.reshape(W.shape)
                    W = W.astype(float)
                    logger.info("Running Randomized Full SVD")
                    sv = np.linalg.svd(W, compute_uv=False)
                    sv = sv.flatten()
                    sv = np.sort(sv)[-n_comp:]    
                    
                    #sv = svd.singular_values_
                    evals = sv*sv
                    if normalize:
                        evals = evals/N
                     
                    all_evals.extend(evals)
                                       
        return np.sort(np.array(all_evals))
    

    
    
    
    def analyze_combined_weights(self, weights, layerid, min_size, max_size,
                normalize, glorot_fix, plot, mp_fit,  conv2d_norm, N, M, n_comp, 
                fit_bulk):
        """Analyzes weight matrices, combined as if they are 1 giant matrices
        Computes PL alpha fits and  various norm metrics
         - alpha
         - alpha_weighted
         
         - Frobenius norm 
         - Spectral Norm
         - p-norm / Shatten norm
         - Soft Rank / Stable Rank
        
        Assumes all matrices have the same shape, (N x M), N > M.
        
        For now, retains the old idea that we have layer_id and slice_id=0 (always)
        res[0][''] = ...
         """
         
        res = {}
        count = len(weights)
        if count == 0:
            return res

        # slice_id
        i = 0
        res[i] = {}
        
        # TODO:  add conv2D ?  How to integrate into this code base ?
        # how deal with glorot norm and normalization ?
        # what is Q ?  n_comps x something ?
        
        # assume all weight matrices have the same shape
        W = weights[0]
        #M, N = np.min(W.shape), np.max(W.shape)
        Q=N/M
        
        res[i]["N"] = N
        res[i]["M"] = M
        res[i]["Q"] = Q  
        summary = []
         
        # TODO:  start method here, have a pre-method that creates the matrices of weights
        # pass N, M in 
        
        #
        # Get combined eigenvalues for all weight matrices, using SVD
        # returns singular values to
        #
 
        check, checkTF = self.glorot_norm_check(W, N, M, count) 
        res[i]['check'] = check
        res[i]['checkTF'] = checkTF
        
        
        evals, sv_max, rank_loss = self.combined_eigenvalues(weights, n_comp, min_size, max_size, normalize, glorot_fix, conv2d_norm)  
        
        num_evals = len(evals)     
        if num_evals < min_size:
            logger.info("skipping layer, num evals {} < {} min size".format(num_evals, min_size))
            return res
        #elif num_evals > max_size:
        #    logger.info("skipping layer, num evals {} > {} max size".format(num_evals, max_size))
        #    return res
        
        
        lambda_max = np.max(evals)
        
        res[i]["sv_max"] = sv_max
        res[i]["rank_loss"] = rank_loss
        
        # this should never happen, but just in case
        if len(evals) < 2: 
            return res
        
        #
        # Power law fit
        #
        title = "Weight matrix ({}x{})  layer ID: {}".format(N, M, layerid)
        alpha, xmin, xmax, D, sigma = self.fit_powerlaw(evals, plot=plot, title=title)    
        
        res[i]["alpha"] = alpha
        res[i]["D"] = D
        res[i]["sigma"] = sigma

        res[i]["xmin"] = xmin
        res[i]["xmax"] = xmax
        res[i]["lambda_min"] = np.min(evals)
        res[i]["lambda_max"] =lambda_max
        
        
        alpha_weighted = alpha * np.log10(lambda_max)
        res[i]["alpha_weighted"] = alpha_weighted
        
        #
        # other metrics
        #
                  
        norm = np.sum(evals)
        res[i]["norm"] = norm
        lognorm = np.log10(norm)
        res[i]["lognorm"] = lognorm
        
        logpnorm = np.log10(np.sum([ev**alpha for ev in evals]))
        res[i]["logpnorm"] = logpnorm
            
        res[i]["spectralnorm"] = lambda_max
        res[i]["logspectralnorm"] = np.log10(lambda_max)

        summary.append("Weight matrix  ({},{}): LogNorm: {} ".format( M, N, lognorm) )
                
        softrank = norm**2 / sv_max**2
        softranklog = np.log10(softrank)
        softranklogratio = lognorm / np.log10(sv_max)
        res[i]["softrank"] = softrank
        res[i]["softranklog"] = softranklog
        res[i]["softranklogratio"] = softranklogratio
        
        summary += "{}. Softrank: {}. Softrank log: {}. Softrank log ratio: {}".format(summary, softrank, softranklog, softranklogratio)
        res[i]["summary"] = "\n".join(summary)
        for line in summary:
            logger.debug("    {}".format(line))
            
        # overlay plot with randomized matrix on log scale
        num_replicas = 1
        if len(evals)*len(weights) < 100: 
            num_replicas = 10
            logger.info("Using {} random replicas".format(num_replicas))

            
        rand_evals = self.random_eigenvalues(weights, n_comp, num_replicas=num_replicas, 
                                              min_size=min_size, max_size=max_size, 
                                              normalize=normalize, glorot_fix=glorot_fix, conv2d_norm=conv2d_norm)

        res[i]["max_rand_eval"] = np.max(rand_evals)
        res[i]["min_rand_eval"] = np.max(rand_evals)
        
        if plot:
            self.plot_random_esd(evals, rand_evals, title)       
        
        # power law fit, with xmax = random bulk edge
        # experimental fit
        #
        # note: this only works if we have more than a few eigenvalues < xmax and > xmin
        alpha2, D2, xmin2, xmax2 = None, None, None, None
        if fit_bulk:
            logger.info("fitting bulk")
            try:
                xmax = np.max(rand_evals)
                num_evals_left = len(evals[evals < xmax])
                if  num_evals_left > 10: # not sure on this yet
                    title = "Weight matrix ({}x{})  layer ID: {} Fit2".format(N, M, layerid)
                    #alpha2, D2, xmin2, xmax2 = self.fit_powerlaw(evals, xmin='peak', xmax=xmax, plot=plot, title=title) 
                    alpha2, D2, xmin2, xmax2 = self.fit_powerlaw(evals, xmin=None, xmax=xmax, plot=plot, title=title)  
     
                    res[i]["alpha2"] = alpha2
                    res[i]["D2"] = D2
                    alpha2_weighted = alpha2 * np.log10(xmax)
                    res[i]["alpha2_weighted"] = alpha2_weighted
            except:
                logger.info("fit2 fails, not sure why")
                pass  
                
        
        return res
            
    def plot_random_esd(self, evals, rand_evals, title):
        """Plot histogram and log histogram of ESD and randomized ESD"""
          
        nonzero_evals = evals[evals > 0.0]
        nonzero_rand_evals = rand_evals[rand_evals > 0.0]
        max_rand_eval = np.max(rand_evals)

        plt.hist((nonzero_evals),bins=100, density=True, color='g', label='original')
        plt.hist((nonzero_rand_evals),bins=100, density=True, color='r', label='random', alpha=0.5)
        plt.axvline(x=(max_rand_eval), color='orange', label='max rand')
        plt.title(r"ESD and Randomized (ESD $\rho(\lambda)$)" + "\nfor {} ".format(title))                  
        plt.legend()
        plt.show()

        plt.hist(np.log10(nonzero_evals),bins=100, density=True, color='g', label='original')
        plt.hist(np.log10(nonzero_rand_evals),bins=100, density=True, color='r', label='random', alpha=0.5)
        plt.axvline(x=np.log10(max_rand_eval), color='orange', label='max rand')
        plt.title(r"Log10 ESD and Randomized (ESD $\rho(\lambda))$" + "\nfor {} ".format(title))                  
        plt.legend()
        plt.show()
        
        
        
          
        
    # Mmybe should be static function    
    def calc_rank_loss(self, singular_values, M, lambda_max):
        """compute the rank loss for these singular given the tolerances
        """
        sv = singular_values
        tolerance = lambda_max * M * np.finfo(np.max(sv)).eps
        return np.count_nonzero(sv > tolerance, axis=-1)
        
            
    def fit_powerlaw(self, evals, xmin=None, xmax=None, plot=True, title="", sample=True):
        """Fit eigenvalues to powerlaw
        
            if xmin is 
                'auto' or None, , automatically set this with powerlaw method
                'peak' , try to set by finding the peak of the ESD on a log scale
            
            if xmax is 'auto' or None, xmax = np.max(evals)
                     
         """
             
        num_evals = len(evals)
        logger.info("fitting power law on {} eigenvalues".format(num_evals))
        
        # TODO: replace this with a robust sampler / stimator
        # requires a lot of refactoring below
        if  sample and num_evals > MAX_NUM_EVALS:
            logger.info("chosing {} eigenvalues from {} ".format(MAX_NUM_EVALS, len(evals)))
            evals = np.random.choice(evals, size=MAX_NUM_EVALS)
                    
        if xmax=='auto' or xmax is None:
            xmax = np.max(evals)
            
        if xmin=='auto' or xmin is None:
            fit = powerlaw.Fit(evals, xmax=xmax, verbose=False)
        elif xmin=='peak':
            nz_evals = evals[evals > 0.0]
            num_bins = 100# np.min([100, len(nz_evals)])
            h = np.histogram(np.log10(nz_evals),bins=num_bins)
            ih = np.argmax(h[0])
            xmin2 = 10**h[1][ih]
            xmin_range = (0.95*xmin2, 1.05*xmin2)
            fit = powerlaw.Fit(evals, xmin=xmin_range, xmax=xmax, verbose=False)   
        else:
            fit = powerlaw.Fit(evals, xmin=xmin, xmax=xmax, verbose=False)
            
            
        alpha = fit.alpha 
        D = fit.D
        sigma = fit.sigma
        xmin = fit.xmin
        xmax = fit.xmax
        
        
        if plot:
            fig2 = fit.plot_pdf(color='b', linewidth=2)
            fit.power_law.plot_pdf(color='b', linestyle='--', ax=fig2)
            fit.plot_ccdf(color='r', linewidth=2, ax=fig2)
            fit.power_law.plot_ccdf(color='r', linestyle='--', ax=fig2)
        
            title = "Power law fit for {}\n".format(title) 
            title = title + r"$\alpha$={0:.3f}; ".format(alpha) + r"KS_distance={0:.3f}".format(D) +"\n"
            plt.title(title)
            plt.show()
    
            # plot eigenvalue histogram
            num_bins = 100#np.min([100,len(evals)])
            plt.hist(evals, bins=num_bins, density=True)
            plt.title(r"ESD (Empirical Spectral Density) $\rho(\lambda)$" + "\nfor {} ".format(title))                  
            plt.axvline(x=fit.xmin, color='red', label='xmin')
            plt.legend()
            plt.show()


            # plot log eigenvalue histogram
            nonzero_evals = evals[evals > 0.0]
            plt.hist(np.log10(nonzero_evals),bins=100, density=True)
            plt.title(r"Log10 ESD (Empirical Spectral Density) $\rho(\lambda)$" + "\nfor {} ".format(title))                  
            plt.axvline(x=np.log10(fit.xmin), color='red')
            plt.axvline(x=np.log10(fit.xmax), color='orange', label='xmax')
            plt.legend()
            plt.show()
    
            # plot xmins vs D
            
            plt.plot(fit.xmins, fit.Ds, label=r'$D$')
            plt.axvline(x=fit.xmin, color='red', label='xmin')
            plt.plot(fit.xmins, fit.sigmas/fit.alphas, label=r'$\sigma /\alpha$', linestyle='--')
            plt.xlabel(r'$x_{min}$')
            plt.ylabel(r'$D,\sigma,\alpha$')
            plt.title("current xmin={:0.3}".format(fit.xmin))
            plt.legend()
            plt.show() 
                          
        return alpha, xmin, xmax, D, sigma
        
         

    def analyze_weights(self, weights, layerid, min_size, max_size,
                        alphas, lognorms, spectralnorms, softranks,  
                        normalize, glorot_fix, plot, mp_fit):
        """Analyzes weight matrices.
        
        Example in Tf.Keras.
            weights = layer.get_weights()
            analyze_weights(weights)
        """

        res = {}
        count = len(weights)
        if count == 0:
            return res


        for i, W in enumerate(weights):
            res[i] = {}
            M, N = np.min(W.shape), np.max(W.shape)
            Q=N/M
            res[i]["N"] = N
            res[i]["M"] = M
            res[i]["Q"] = Q
            lambda0 = None

            check, checkTF = self.glorot_norm_check(W, N, M, count) 
            res[i]['check'] = check
            res[i]['checkTF'] = checkTF
            # assume receptive field size is count
            if glorot_fix:
                W = self.glorot_norm_fix(W, N, M, count)
            else:
                # probably never needed since we always fix for glorot
                W = W * np.sqrt(count/2.0) 


            if spectralnorms: #spectralnorm is the max eigenvalues
                
                svd = TruncatedSVD(n_components=1, n_iter=7, random_state=10)
                svd.fit(W)
                sv = svd.singular_values_
                sv_max = np.max(sv)
                evals = sv*sv
                if normalize:
                    evals = evals/N

                lambda0 = np.max(evals)
                res[i]["spectralnorm"] = lambda0
                res[i]["logspectralnorm"] = np.log10(lambda0)

            if M < min_size:
                summary = "Weight matrix {}/{} ({},{}): Skipping: too small (<{})".format(i+1, count, M, N, min_size)
                res[i]["summary"] = summary 
                logger.debug("    {}".format(summary))
                continue

            #if max_size > 0 and M > max_size:
            #    summary = "Weight matrix {}/{} ({},{}): Skipping: too big (testing) (>{})".format(i+1, count, M, N, max_size)
            #    res[i]["summary"] = summary 
            #    logger.info("    {}".format(summary))
            #    continue

            summary = []
                
            logger.debug("    Weight matrix {}/{} ({},{}): Analyzing ..."
                     .format(i+1, count, M, N))
            
            if alphas:

                svd = TruncatedSVD(n_components=M-1, n_iter=7, random_state=10)
                
                try:
                    svd.fit(W) 
                except:
                    W = W.astype(float)
                    svd.fit(W)
                    
                sv = svd.singular_values_
                sv_max = np.max(sv)
                evals = sv*sv
                if normalize:
                    evals = evals/N

                # Other (slower) way of computing the eigen values:
                # X = np.dot(W.T,W)/N
                #evals2 = np.linalg.eigvals(X)
                #res[i]["lambda_max2"] = np.max(evals2)

                #TODO:  add alpha2

                lambda_max = np.max(evals)
                fit = powerlaw.Fit(evals, xmax=lambda_max, verbose=False)
                alpha = fit.alpha 
                D = fit.D
                xmin = fit.xmin
                res[i]["alpha"] = alpha
                res[i]["D"] = D
                res[i]["xmin"] = xmin
                res[i]["lambda_min"] = np.min(evals)
                res[i]["lambda_max"] = lambda_max
                alpha_weighted = alpha * np.log10(lambda_max)
                res[i]["alpha_weighted"] = alpha_weighted
                tolerance = lambda_max * M * np.finfo(np.max(sv)).eps
                res[i]["rank_loss"] = np.count_nonzero(sv > tolerance, axis=-1)
                
                logpnorm = np.log10(np.sum([ev**alpha for ev in evals]))
                res[i]["logpnorm"] = logpnorm

                nz_evals = evals[evals > 0.0]
                num_bins = np.min([100, len(nz_evals)])
                h = np.histogram(np.log10(nz_evals),bins=num_bins)
                ih = np.argmax(h[0])
                xmin2 = 10**h[1][ih]
                if xmin2 > xmin:
                    logger.info("resseting xmin2 to xmin")
                    xmin2 = xmin

                fit2 = powerlaw.Fit(evals, xmin=xmin2, xmax=lambda_max, verbose=False)
                alpha2 = fit2.alpha
                D2 = fit2.D
                res[i]["alpha2"] = alpha2
                res[i]["D2"] = D2
                res[i]["xmin2"] = fit2.xmin
                res[i]["alpha2_weighted"] =  alpha2 * np.log10(lambda_max)

                summary.append("Weight matrix {}/{} ({},{}): Alpha: {}, Alpha Weighted: {}, D: {}, pNorm {}".format(i+1, count, M, N, alpha, alpha_weighted, D, logpnorm))

                #if alpha < alpha_min or alpha > alpha_max:
                #    message = "Weight matrix {}/{} ({},{}): Alpha {} is in the danger zone ({},{})".format(i+1, count, M, N, alpha, alpha_min, alpha_max)
                #    logger.debug("    {}".format(message))

                if plot:
                    fig2 = fit.plot_pdf(color='b', linewidth=2)
                    fit.power_law.plot_pdf(color='b', linestyle='--', ax=fig2)
                    fit.plot_ccdf(color='r', linewidth=2, ax=fig2)
                    fit.power_law.plot_ccdf(color='r', linestyle='--', ax=fig2)
                    fit2.plot_pdf(color='g', linewidth=2)
#                    plt.title("Power law fit for Weight matrix {}/{} (layer ID: {})".format(i+1, count, layerid))
                    title = "Power law fit for Weight matrix {}/{} (layer ID: {})\n".format(i+1, count, layerid) 
                    title = title + r"$\alpha$={0:.3f}; ".format(alpha) + r"KS_distance={0:.3f}".format(D) +"\n"
                    title = title + r"$\alpha2$={0:.3f}; ".format(alpha2) + r"KS_distance={0:.3f}".format(D2)
                    plt.title(title)
                    plt.show()

                    # plot eigenvalue histogram
                    plt.hist(evals, bins=100, density=True)
#                    plt.title(r"ESD (Empirical Spectral Density) $\rho(\lambda)$" + " for Weight matrix {}/{} (layer ID: {})".format(i+1, count, layerid))
                    plt.title(r"ESD (Empirical Spectral Density) $\rho(\lambda)$" + "\nfor Weight matrix ({}x{}) {}/{} (layer ID: {})".format(N, M, i+1, count, layerid))                    
                    plt.axvline(x=fit.xmin, color='red')
                    plt.axvline(x=fit2.xmin, color='green')
                    plt.show()

                    nonzero_evals = evals[evals > 0.0]
                    plt.hist(np.log10(nonzero_evals),bins=100, density=True)
#                    plt.title("Eigen Values for Weight matrix {}/{} (layer ID: {})".format(i+1, count, layerid))
                    plt.title("Logscaling Plot of Eigenvalues\nfor Weight matrix ({}X{}) {}/{} (layer ID: {})".format(N, M, i+1, count, layerid))
                    plt.axvline(x=np.log10(fit.xmin), color='red')
                    plt.axvline(x=np.log10(xmin2), color='green')
                    plt.show()
                
            if mp_fit:
#                if Q == 1:
#                    ## Quarter-Circle Law
#                    sv = svd.singular_values_
#                    to_plot = np.sqrt(sv*sv/N)
#                else:
#                    to_plot = sv*sv/N
#                w_unnorm = W*np.sqrt(N + M)/np.sqrt(2*N)
                
                if not alphas:
                    #W = self.normalize(W, N, M, count)
                    svd = TruncatedSVD(n_components=M-1, n_iter=7, random_state=10)
                    svd.fit(W) 
                    sv = svd.singular_values_
                    sv_max = np.max(sv)
                    evals = sv*sv
                    if normalize:
                        evals = evals/N
                    lambda_max = np.max(evals)
                
                to_plot = evals.copy()
                
                bw = 0.1
#                s1, f1 = RMT_Util.fit_mp(to_plot, Q, bw = 0.01)  
#                s1, f1 = fit_density(to_plot, Q, bw = bw)  
                s1, f1 = fit_density_with_range(to_plot, Q, bw = bw)
                
                res[i]['sigma_mp'] = s1
                
                bulk_edge = (s1 * (1 + 1/np.sqrt(Q)))**2
                
                spikes = sum(to_plot > bulk_edge)
                res[i]['numofSpikes'] = spikes
                res[i]['ratio_numofSpikes'] = spikes / (M - 1)
                
                softrank_mp = bulk_edge / lambda_max
                res[i]['softrank_mp'] = softrank_mp
                
                if plot:
                    
                    if Q == 1:
                        fit_law = 'QC SSD'
                        
                        #Even if the quarter circle applies, still plot the MP_fit
                        plot_density(to_plot, s1, Q, method = "MP")
                        plt.legend([r'$\rho_{emp}(\lambda)$', 'MP fit'])
                        plt.title("MP ESD, sigma auto-fit for Weight matrix {}/{} (layer ID: {})\nsigma_fit = {}, softrank_mp = {}".format(i+1, count, layerid, round(s1, 6), round(softrank_mp, 3)))
                        plt.show()
                        
                    else:
                        fit_law = 'MP ESD'
#                        RMT_Util.plot_ESD_and_fit(model=None, eigenvalues=to_plot, 
#                                                  Q=Q, num_spikes=0, sigma=s1)
                    plot_density_and_fit(model=None, eigenvalues=to_plot, 
                                         Q=Q, num_spikes=0, sigma=s1, verbose = False)
                    plt.title("{}, sigma auto-fit for Weight matrix {}/{} (layer ID: {})\nsigma_fit = {}, softrank_mp = {}".format(fit_law, i+1, count, layerid, round(s1, 6), round(softrank_mp, 3)))
                    plt.show()
                        
            if lognorms:
                norm = np.linalg.norm(W) #Frobenius Norm
                res[i]["norm"] = norm
                lognorm = np.log10(norm)
                res[i]["lognorm"] = lognorm

                X = np.dot(W.T,W)                
                if normalize:
                    X = X/N
                normX = np.linalg.norm(X) #Frobenius Norm
                res[i]["normX"] = normX
                lognormX = np.log10(normX)
                res[i]["lognormX"] = lognormX

                summary.append("Weight matrix {}/{} ({},{}): LogNorm: {} ; LogNormX: {}".format(i+1, count, M, N, lognorm, lognormX))
                
                if softranks: 
                    softrank = norm**2 / sv_max**2
                    softranklog = np.log10(softrank)
                    softranklogratio = lognorm / np.log10(sv_max)
                    res[i]["softrank"] = softrank
                    res[i]["softranklog"] = softranklog
                    res[i]["softranklogratio"] = softranklogratio
                    summary += "{}. Softrank: {}. Softrank log: {}. Softrank log ratio: {}".format(summary, softrank, softranklog, softranklogratio)

                        

            res[i]["summary"] = "\n".join(summary)
            for line in summary:
                logger.debug("    {}".format(line))

        return res
    
    
    
