"""
Copyright (c) 2012, Martin S. Lindner, LindnerM@rki.de, 
Robert Koch-Institut, Germany,
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * The name of the author may not be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL MARTIN S. LINDNER BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import sys
import numpy as np
import numpy.linalg as la
import scipy.optimize as opt
import parmap


def similarity_correction(sim, reads, N):
    """ Calculate corrected abundances given a similarity matrix and observations using optimization.

    Input:
    sim: [numpy.array (M,M)]: with pairwise similarities between species
    reads [numpy.array (M,)]: with number of observed reads for each species
    N [int]: total number of reads

    Output:
    abundances [numpy.array (M,)]: estimated abundance of each species in the sample
    """
   
    # transform reads to abundances and rename similarity matrix
    A = sim
    r = reads.astype(np.float) / N

    rng = range(len(reads))
    
    # Now solve the optimization problem: min_c |Ac-r|^2 s.t. c_i >= 0 and 1-sum(c_i) >= 0
    # construct objective function
    def objective (c):
        # numpy implementation of objective function |A*c-r|^2
        return np.sum(np.square(np.dot(A,c)-r))
    
    # construct constraints
    def build_con(k):
        # constraint: k'th component of x should be >0
        return lambda c: c[k]
    cons = [build_con(k) for k in rng]
    # constraint: the sum of all components of x should not exceed 1
    cons.append( lambda c: 1-np.sum(c) )
    
    # initial guess
    c_0 = np.array([0.5 for i in range(len(reads))])

    # finally: optimization procedure
    abundances = opt.fmin_cobyla(objective, c_0, cons, disp=0, rhoend=1e-10, maxfun=10000)

    return abundances
    



def similarity_correction_smp(sim, reads, N, subsets=1):
    """ Calculate corrected abundances given a similarity matrix and observations
        using optimization. Sample subsets of the reads to obtain a more stable
        estimation of the abundance.

    Input:
    sim: [numpy.array (M,M)]: with pairwise similarities between species
    reads [numpy.array (M,N)]: read mapping information about every species
    N [int]: total number of reads
    subsets [int]: divide the reads in subsets and use the median of the estimated abundances

    Output:
    abundances [numpy.array (M,)]: estimated relative abundance of each species in the sample
    """
    # Each subset consists of every subsets'th read. Count the number of matching reads
    # per species in each subset
    M = reads.shape[0]
    reads_sbs = np.zeros((M, subsets))
    for s in range(subsets):
        reads_sbs[:,s] = np.sum(reads[:,s::subsets], axis=1)

    N_sbs = np.ceil(float(N)/subsets)

    # calculate the corrected abundances for each subset
    sbs_corr = np.zeros( (M,subsets) )
    for s in range(subsets):
        sbs_corr[:,s] = similarity_correction(sim, reads_sbs[:,s], N_sbs)

    abundances = np.median(sbs_corr,axis=1)
    
    return abundances



def bootstrap_similarity_matrix(mapped_reads):
    """
    Calculate a similarity matrix by bootstrapping.

    INPUT:
    mapped_reads:    mapping information as generated by similarity_matrix_raw().

    OUTPUT:
    d_matrix:        similarity matrix. Ordering is the same as in 'names' used in the
                     similarity_matrix_raw() function.
    """
    # get the number of sequences and simulated reads from the shape of the mapped reads matrix
    num_reads = mapped_reads.shape[2]
    num_seq = mapped_reads.shape[0]

    # create a bootstrap index vector
    bootstrap_indices = np.random.randint(num_reads, size=(num_reads,))

    # count number of mapped reads in bootstrap sample
    counts = np.sum( mapped_reads[:,:,bootstrap_indices], axis=2 )    
    
    # normalize read counts and build similarity matrix
    s_matrix = np.zeros((num_seq,num_seq))
    for i in range(num_seq):
        for j in range(num_seq):
            # similarity matrix entries as described in the paper
            s_matrix[i,j] = float(counts[j,i])/counts[i,i]

    return s_matrix



def bootstrap(reads, smat_raw, B, test_c=0.01):
    """
    Similarity correction using a bootstrapping procedure for more robust corrections and error
    estimates.

    Args:
    reads -- [numpy.array (M,N)] array with mapping information; reads[m,n]==1,
              if read n mapped to species m.
    smat_raw -- mapping information for similarity matrix. species have same ordering as reads array
    B -- Number of bootstrap samples
    test_c -- For testing: treat species as not present, if estimated concentration is below test_c.
    
    Return:
    [p_values, abundances, variances] -- list of floats
    
    """
    # M: Number of species, N: Number of reads
    M,N = reads.shape 

    # initialize arrays to store results
    found = np.zeros( (B,M) )
    corr  = np.zeros( (B,M) )
    fails = np.zeros( (B,M) )

    for b in range(B):
        sys.stderr.write("... bootstrapping {} of {}\n".format(b+1,B))
        # select a bootstrap sample 
        random_set = np.random.randint(N,size=N)

        # count the number of matching reads in bootstrap sample
        found[b,:] = np.sum(reads[:,random_set], axis=1)
        #for r in range(N):
        #    found[b,:] += reads[:,random_set[r]]

        # bootstrap a similarity matrix
        smat = bootstrap_similarity_matrix(smat_raw)
        
        # calculate abundances
        corr[b,:] = similarity_correction(smat,found[b,:],N)

        # check if the calculated abundance is below the test abundance
        fails[b,:] = corr[b,:] < test_c

    p_values = np.mean(fails, axis=0)
    abundances = np.mean(corr, axis=0)
    variances = np.var(corr, axis=0)
    return p_values, abundances, variances



def _boot_iteration(b, reads, smat_raw, test_c, B, M, N):
    """One bootstrap iteration for bootstrap_par function.
    See bootstrap_par for arg doc."""    
    sys.stderr.write("...bootstrapping {} of {}\n".format(b+1,B))

    # arrays for results
    res = {'found' : np.zeros( (1,M) ),
           'corr' : np.zeros( (1,M) ),
           'fails' : np.zeros( (1,M) )}
    
    # select a bootstrap sample 
    random_set = np.random.randint(N,size=N)
    
    # count the number of matching reads in bootstrap sample
    res['found'][0,:] = np.sum(reads[:,random_set], axis=1)

    # bootstrap a similarity matrix
    smat = bootstrap_similarity_matrix(smat_raw)
    
    # calculate abundances
    res['corr'][0,:] = similarity_correction(smat,res['found'][0,:],N)
    
    # check if the calculated abundance is below the test abundance
    res['fails'][0,:] = res['corr'][0,:] < test_c
    
    # return
    return res


def bootstrap_par(reads, smat_raw, B, test_c=0.01, nprocs=1):
    """
    Similarity correction using a bootstrapping procedure for more robust corrections and error
    estimates. Bootstrapping conducted in parallel.

    Args:
    reads -- [numpy.array (M,N)] array with mapping information; reads[m,n]==1,
             if read n mapped to species m.
    smat_raw -- mapping information for similarity matrix. species have same ordering as reads array
    B -- Number of bootstrap samples
    test_c -- For testing: treat species as not present, if estimated concentration is below test_c.
    nprocs -- Number of parallel bootstrap processes to perform.

    Return:
    [p_values, abundances, variances] -- list of floats
    
    """
    # M: Number of species, N: Number of reads
    M,N = reads.shape 

    resList = parmap.map(_boot_iteration, range(B), reads, smat_raw, test_c, B, M, N, processes=nprocs)

    # merging arrays (found, core, fails)
    found = np.concatenate( [x['found'] for x in resList] )
    corr = np.concatenate( [x['corr'] for x in resList] )
    fails = np.concatenate( [x['fails'] for x in resList] )    
    
    # calculations
    p_values = np.mean(fails, axis=0)
    abundances = np.mean(corr, axis=0)
    variances = np.var(corr, axis=0)
    return p_values, abundances, variances

    
