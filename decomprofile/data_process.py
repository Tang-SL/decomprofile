#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Sep 10 09:10:39 2020

@author: Xuheng Ding

A class to process the data
"""
from __future__ import print_function

import numpy as np
import matplotlib.pyplot as plt
import astropy.io.fits as pyfits
from astropy.wcs import WCS
from decomprofile.tools.measure_tools import measure_bkg
from decomprofile.tools.cutout_tools import cut_center_auto, cutout
from copy import deepcopy
from matplotlib.colors import LogNorm
from decomprofile.tools.astro_tools import plt_fits, read_pixel_scale

import sys
from packaging import version

class DataProcess(object):
    """
    A class to Process the data, including the following feature:
        - automaticlly estimate and remove background light.
        - cutout the target photo stamp.
        - search all the avaiable PSF in the field.
        - creat mask for the objects.
        - measure the target surface brightness profile, PSF FWHM, background.
    """
    def __init__(self, fov_image=None, target_pos = None, pos_type = 'pixel', header=None, exptime = None, fov_noise_map = None,
                 rm_bkglight = False, if_plot = False, zp = None, **kwargs):
        """
        Parameter
        --------
            data_image: 2D array
            The field of view image of the data.
            
            target_pos: list or tuple or array, length = 2
            The position of the target.
            
            pos_type: string, 'pixel' or 'wcs'
            Define the position of the target, i.e., if the position is in 'pixel' or 'wcs'.
                
            header: io.fits.header
            The header information given by the fits file. 
            Note: should including the exposure time and WCS information.
            
            exptime: float or 2D array
            The exposure time of the data in (s) a the exptime_map
            
        """
        if target_pos is not None:
            if pos_type == 'pixel':
                self.target_pos = target_pos
            elif pos_type == 'wcs':
                wcs = WCS(header)
                self.target_pos = wcs.all_world2pix([[target_pos[0], target_pos[1]]], 1)[0]
            else:
                raise ValueError("'pos_type' is should be either 'pixel' or 'wcs'.")
            self.target_pos = np.int0(self.target_pos)
        else:
            raise ValueError("'target_pos' must be assigned.")

        self.exptime = exptime
        self.if_plot = if_plot    
        self.header = header
        if header is not None:
            self.deltaPix = read_pixel_scale(header)
            if self.deltaPix == 3600.:
                print("WARNING: pixel size could not read from the header! ")
        if fov_image is not None and rm_bkglight == True:
            bkglight = measure_bkg(fov_image, if_plot=if_plot, **kwargs)
            fov_image = fov_image-bkglight
        self.fov_image = fov_image
        self.fov_noise_map = fov_noise_map
        
        self.psf_id_for_fitting = 0 #The psf id in the PSF_list that would be used in the fitting.
        if zp is None:
            print("Zeropoint value is not provided, use 27.0 to calculate magnitude.")
            self.zp = 27.0
        else:
            self.zp = zp

    def generate_target_materials(self, cut_kernel = None,  radius=None, radius_list = None,
                                  bkg_std = None, create_mask = False, if_plot=None, **kwargs):
        """
        Produce the materials that would be used for the fitting.
        
        Parameter
        --------
            radius: int or float
            The radius of aperture to cutout the target
            cut_kernel: None or 'center_gaussian' or 'center_bright'
                if is None, directly cut.
                if is 'center_gaussian', fit central as Gaussian to cut the Gaussian center.
                if is 'center_bright', cut the brightest pixel in the center
            bkg_std: The blash of blash
            
        """
        if if_plot == None:
            if_plot = self.if_plot
            
        if radius == None:
            if radius_list == None:
                radius_list = [30, 35, 40, 45, 50, 60, 70]
            for rad in radius_list:
                from decomprofile.tools.measure_tools import fit_data_oneD_gaussian
                _cut_data = cutout(image = self.fov_image, center = self.target_pos, radius=rad)
                edge_data = np.concatenate([_cut_data[0,:],_cut_data[-1,:],_cut_data[:,0], _cut_data[:,-1]])
                gauss_mean, gauss_1sig = fit_data_oneD_gaussian(edge_data, ifplot=False)
                up_limit = gauss_mean + 2 * gauss_1sig
                percent = np.sum(edge_data>up_limit)/float(len(edge_data))
                if percent<0.03:
                    break
            radius = rad
                
        if if_plot == True:
            print("Plot target cut out zoom in:")
        if cut_kernel is not None:
            target_stamp, self.target_pos = cut_center_auto(image=self.fov_image, center= self.target_pos, 
                                              kernel = cut_kernel, radius=radius,
                                              return_center=True, if_plot=if_plot)
        else:
            target_stamp = cutout(image = self.fov_image, center = self.target_pos, radius=radius)
        
        if self.fov_noise_map is not None:
            self.noise_map = cutout(image = self.fov_noise_map, center = self.target_pos, radius=radius)
        else:
            if bkg_std == None:
                from decomprofile.tools.measure_tools import esti_bgkstd
                target_2xlarger_stamp = cutout(image=self.fov_image, center= self.target_pos, radius=radius*2)
                self.bkg_std = esti_bgkstd(target_2xlarger_stamp, if_plot=if_plot)
            exptime = deepcopy(self.exptime)
            if exptime is None:
                if 'EXPTIME' in self.header.keys():
                    exptime = self.header['EXPTIME']
                else:
                    raise ValueError("No Exposure time information in the header, should input a value.")
            if isinstance(exptime, np.ndarray):
                exptime_stamp = cutout(image=self.exptime, center= self.target_pos, radius=radius)
            noise_map = np.sqrt(abs(target_stamp/exptime_stamp) + self.bkg_std**2)
            self.noise_map = noise_map
        
        target_mask = np.ones_like(target_stamp)
        from decomprofile.tools.measure_tools import detect_obj, mask_obj
        apertures = detect_obj(target_stamp, if_plot=create_mask, **kwargs)
        if create_mask == True:
            select_idx = str(input('Input directly the a obj idx to mask, use space between each id:\n'))
            if sys.version_info.major > 2:
                select_idx = [int(select_idx[i]) for i in range(len(select_idx)) if select_idx[i].isnumeric()]
            else:
                select_idx = [int(select_idx[i]) for i in range(len(select_idx)) if select_idx[i].isdigit()]
            apertures_ = [apertures[i] for i in select_idx]
            apertures = [apertures[i] for i in range(len(apertures)) if i not in select_idx]
            mask_list = mask_obj(target_stamp, apertures_, if_plot=False)
            for i in range(len(mask_list)):
                target_mask *= mask_list[i]
        self.apertures = apertures
        self.target_stamp = target_stamp
        self.target_mask = target_mask
        if if_plot:
            fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 10))
            ax1.imshow(target_stamp, origin='lower', norm=LogNorm())
            ax1.set_title('Cutout target')
            ax2.imshow(self.noise_map, origin='lower', norm=LogNorm())
            ax2.set_title('Noise map')
            ax3.imshow(target_stamp * target_mask, origin='lower', norm=LogNorm())
            ax3.set_title('data * mask')
            plt.show()  
    
    def find_PSF(self, radius = 50, PSF_pos_list = None, pos_type = 'pixel', user_option= False):
        """
        The purpose of this def
        
        Parameter
        --------
            radius: int/float
            The radius of the cutout frames of the PSF. i.e., size = 2*radius + 1
            
            user_option: bool
            only meaningful when PSF_pos_list = None. 
            
        Return
        --------
            A sth sth
        """
        if PSF_pos_list is None:
            from decomprofile.tools.measure_tools import search_local_max, measure_FWHM
            init_PSF_locs_ = search_local_max(self.fov_image)
            init_PSF_locs, FWHMs, fluxs = [], [], []
            for i in range(len(init_PSF_locs_)):
                cut_image = cut_center_auto(self.fov_image, center = init_PSF_locs_[i],
                                            radius=radius)
                _fwhms = measure_FWHM(cut_image , radius = int(radius/5))
                if np.std(_fwhms)/np.mean(_fwhms) < 0.1 :  #Remove the deteced "PSFs" at the edge.
                    init_PSF_locs.append(init_PSF_locs_[i])
                    FWHMs.append(np.mean(_fwhms))
                    fluxs.append(np.sum(cut_image))
            init_PSF_locs = np.array(init_PSF_locs)
            FWHMs = np.array(FWHMs)
            fluxs = np.array(fluxs)
            if hasattr(self, 'target_stamp'):
                target_flux = np.sum(self.target_stamp)
                select_bool = (FWHMs<np.median(FWHMs)*1.5)*(fluxs<target_flux*10)*(fluxs>target_flux/2)
            else:
                select_bool = (FWHMs<np.median(FWHMs)*1.5)
            PSF_locs = init_PSF_locs[select_bool]    
            FWHMs = FWHMs[select_bool]
            fluxs = fluxs[select_bool]
            if user_option == True:
                for i in range(len(PSF_locs)):
                    cut_image = cut_center_auto(self.fov_image, center = PSF_locs[i],
                                                kernel = 'center_gaussian', radius=radius)
                    print('PSF location:', PSF_locs[i])
                    print('id:', i, 'FWHMs:', 
                          np.round(measure_FWHM(cut_image ,radius = int(radius/5)),3),
                          'flux:', round(np.sum(cut_image),1) )
                    plt_fits(cut_image)
                select_idx = str(input('Input directly the a obj idx to mask, use space between each id:\n'))
                select_idx = select_idx.split(" ")
                if sys.version_info.major > 2:
                    select_idx = [int(select_idx[i]) for i in range(len(select_idx)) if select_idx[i].isnumeric()]
                else:
                    select_idx = [int(select_idx[i]) for i in range(len(select_idx)) if select_idx[i].isdigit()]                    
                self.PSF_pos_list = [PSF_locs[i] for i in select_idx]
            else:
                select_idx = [np.where(FWHMs == FWHMs.min())[0][0] ]
                self.PSF_pos_list = [PSF_locs[i] for i in select_idx]                
        else:
            if pos_type == 'pixel':
                self.PSF_pos_list = PSF_pos_list
            elif pos_type == 'wcs':
                wcs = WCS(self.header)
                self.PSF_pos_list = [wcs.all_world2pix([[PSF_pos_list[i][0], PSF_pos_list[i][1]]], 1) for i in range(len(self.PSF_pos_list))]
        self.PSF_list = [cut_center_auto(self.fov_image, center = self.PSF_pos_list[i],
                                          kernel = 'center_gaussian', radius=radius) for i in range(len(self.PSF_pos_list))]

    def profiles_compare(self, **kargs):
        from decomprofile.tools.measure_tools import profiles_compare    
        profiles_compare([self.target_stamp] + self.PSF_list, **kargs)
        
    def plot_overview(self, **kargs):
        from decomprofile.tools.cutout_tools import plot_overview
        if hasattr(self, 'PSF_pos_list'):
            PSF_pos_list = self.PSF_pos_list
        else:
            PSF_pos_list = None
        plot_overview(self.fov_image, center_target= self.target_pos,
                      c_psf_list=PSF_pos_list, **kargs)
    
    def checkout(self):
        checklist = ['deltaPix', 'target_stamp', 'noise_map',  'target_mask', 'PSF_list', 'psf_id_for_fitting']
        ct = 0
        if len(self.PSF_list[self.psf_id_for_fitting]) != 0 and self.PSF_list[self.psf_id_for_fitting].shape[0] != self.PSF_list[self.psf_id_for_fitting].shape[1]:
            print("The PSF is not a box size, will cut it to a box size automatically.")
            cut = int((self.PSF_list[self.psf_id_for_fitting].shape[0] - self.PSF_list[self.psf_id_for_fitting].shape[1])/2)
            if cut>0:
                self.PSF_list[self.psf_id_for_fitting] = self.PSF_list[self.psf_id_for_fitting][cut:-cut,:]
            elif cut<0:
                self.PSF_list[self.psf_id_for_fitting] = self.PSF_list[self.psf_id_for_fitting][:,-cut:cut]
            self.PSF_list[self.psf_id_for_fitting] /= self.PSF_list[self.psf_id_for_fitting].sum()
            if self.PSF_list[self.psf_id_for_fitting].shape[0] != self.PSF_list[self.psf_id_for_fitting].shape[1]:
                raise ValueError("PSF shape is not a square.")
        for name in checklist:
            if not hasattr(self, name):
                print('The keyword of {0} is missing.'.format(name))
                ct = ct+1
        if ct == 0:
            print('The data_process is ready to go to pass to FittingSpecify!')
        
        
    
