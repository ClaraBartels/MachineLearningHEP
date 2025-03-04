#############################################################################
##  © Copyright CERN 2018. All rights not expressly granted are reserved.  ##
##                 Author: Gian.Michele.Innocenti@cern.ch                  ##
## This program is free software: you can redistribute it and/or modify it ##
##  under the terms of the GNU General Public License as published by the  ##
## Free Software Foundation, either version 3 of the License, or (at your  ##
## option) any later version. This program is distributed in the hope that ##
##  it will be useful, but WITHOUT ANY WARRANTY; without even the implied  ##
##     warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    ##
##           See the GNU General Public License for more details.          ##
##    You should have received a copy of the GNU General Public License    ##
##   along with this program. if not, see <https://www.gnu.org/licenses/>. ##
#############################################################################

"""
main script for doing ml optisation
"""
import os
import time
from math import sqrt
import pickle
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from sklearn.model_selection import train_test_split
from sklearn.utils import shuffle
from ROOT import TFile, TCanvas, TH1F, TF1, gROOT  # pylint: disable=import-error,no-name-in-module
from machine_learning_hep.utilities import seldf_singlevar, split_df_sigbkg, createstringselection
from machine_learning_hep.utilities import openfile, selectdfquery, mask_df
from machine_learning_hep.correlations import vardistplot, scatterplot, correlationmatrix
from machine_learning_hep.models import getclf_scikit, getclf_xgboost, getclf_keras
from machine_learning_hep.models import fit, savemodels, readmodels, test, apply, decisionboundaries
from machine_learning_hep.root import write_tree
from machine_learning_hep.mlperformance import cross_validation_mse, plot_cross_validation_mse
from machine_learning_hep.mlperformance import plot_learning_curves, precision_recall
from machine_learning_hep.mlperformance import roc_train_test, plot_overtraining
from machine_learning_hep.optimisation.grid_search import do_gridsearch, perform_plot_gridsearch
from machine_learning_hep.models import importanceplotall, shap_study
from machine_learning_hep.logger import get_logger
from machine_learning_hep.optimization import calc_bkg, calc_signif, calc_eff, calc_sigeff_steps
from machine_learning_hep.correlations import vardistplot_probscan, efficiency_cutscan
from machine_learning_hep.utilities import checkdirlist, checkmakedirlist
from machine_learning_hep.io import parse_yaml, dump_yaml_from_dict


# pylint: disable=too-many-instance-attributes, too-many-statements
class Optimiser: # pylint: disable=too-many-public-methods
    #Class Attribute
    species = "optimiser"

    def __init__(self, data_param, case, typean, model_config, binmin,
                 binmax, raahp, training_var, index):

        self.logger = get_logger()

        dirmcml = data_param["multi"]["mc"]["pkl_skimmed_merge_for_ml_all"]
        dirdataml = data_param["multi"]["data"]["pkl_skimmed_merge_for_ml_all"]
        self.v_bin = data_param["var_binning"]
        #directory
        self.dirmlout = data_param["ml"]["mlout"]
        self.dirmlplot = data_param["ml"]["mlplot"]

        # Check here which steps have been done already
        self.steps_done = None
        self.file_steps_done = os.path.join(self.dirmlout, "steps_done.yaml")
        if os.path.exists(self.file_steps_done):
            self.steps_done = parse_yaml(self.file_steps_done)["done"]
        if self.steps_done is None \
                and (os.listdir(self.dirmlout) or os.listdir(self.dirmlplot)):
            # Backwards compatible
            print(f"rm -r {self.dirmlout}")
            print(f"rm -r {self.dirmlplot}")
            self.logger.fatal("Please remove above directories as indicated above first and " \
                    "run again")

        #ml file names
        self.n_reco = data_param["files_names"]["namefile_reco"]
        self.n_reco = self.n_reco.replace(".pkl", "_%s%d_%d.pkl" % (self.v_bin, binmin, binmax))
        self.n_evt = data_param["files_names"]["namefile_evt"]
        self.n_evt_count_ml = data_param["files_names"].get("namefile_evt_count", "evtcount.yaml")
        self.n_gen = data_param["files_names"]["namefile_gen"]
        self.n_gen = self.n_gen.replace(".pkl", "_%s%d_%d.pkl" % (self.v_bin, binmin, binmax))
        self.n_treetest = data_param["files_names"]["treeoutput"]
        self.n_reco_applieddata = data_param["files_names"]["namefile_reco_applieddata"]
        self.n_reco_appliedmc = data_param["files_names"]["namefile_reco_appliedmc"]
        # ml files
        self.f_gen_mc = os.path.join(dirmcml, self.n_gen)
        self.f_reco_mc = os.path.join(dirmcml, self.n_reco)
        self.f_evt_mc = os.path.join(dirmcml, self.n_evt)
        self.f_reco_data = os.path.join(dirdataml, self.n_reco)
        self.f_evt_count_ml = os.path.join(dirdataml, self.n_evt_count_ml)
        print("HERE")
        print(self.f_evt_count_ml)
        self.f_reco_applieddata = os.path.join(self.dirmlout, self.n_reco_applieddata)
        self.f_reco_appliedmc = os.path.join(self.dirmlout, self.n_reco_appliedmc)
        #variables
        self.v_all = data_param["variables"]["var_all"]
        self.v_train = training_var
        self.v_selected = data_param["variables"].get("var_selected", None)
        if self.v_selected:
            self.v_selected = self.v_selected[index]
        self.v_bound = data_param["variables"]["var_boundaries"]
        self.v_sig = data_param["variables"]["var_signal"]
        self.v_invmass = data_param["variables"]["var_inv_mass"]
        self.v_cuts = data_param["variables"].get("var_cuts", [])
        self.v_corrx = data_param["variables"]["var_correlation"][0]
        self.v_corry = data_param["variables"]["var_correlation"][1]
        self.v_isstd = data_param["bitmap_sel"]["var_isstd"]
        self.v_ismcsignal = data_param["bitmap_sel"]["var_ismcsignal"]
        self.v_ismcprompt = data_param["bitmap_sel"]["var_ismcprompt"]
        self.v_ismcfd = data_param["bitmap_sel"]["var_ismcfd"]
        self.v_ismcbkg = data_param["bitmap_sel"]["var_ismcbkg"]
        #parameters
        self.p_case = case
        self.p_typean = typean
        self.p_nbkg = data_param["ml"]["nbkg"]
        self.p_nsig = data_param["ml"]["nsig"]
        self.p_tagsig = data_param["ml"]["sampletagforsignal"]
        self.p_tagbkg = data_param["ml"]["sampletagforbkg"]
        self.p_binmin = binmin
        self.p_binmax = binmax
        self.p_npca = None
        self.p_mltype = data_param["ml"]["mltype"]
        self.p_nkfolds = data_param["ml"]["nkfolds"]
        self.p_ncorescross = data_param["ml"]["ncorescrossval"]
        self.rnd_shuffle = data_param["ml"]["rnd_shuffle"]
        self.rnd_splt = data_param["ml"]["rnd_splt"]
        self.test_frac = data_param["ml"]["test_frac"]
        self.p_plot_options = data_param["variables"].get("plot_options", {})
        self.p_dofullevtmerge = data_param["dofullevtmerge"]

        self.p_evtsel = data_param["ml"]["evtsel"]
        self.p_triggersel_mc = data_param["ml"]["triggersel"]["mc"]
        self.p_triggersel_data = data_param["ml"]["triggersel"]["data"]

        #dataframes
        self.df_mc = None
        self.df_mcgen = None
        self.df_data = None
        self.arraydf = None
        self.df_sig = None
        self.df_bkg = None
        self.df_ml = None
        self.df_mltest = None
        self.df_mltrain = None
        self.df_sigtrain = None
        self.df_sigtest = None
        self.df_bkgtrain = None
        self.df_bktest = None
        self.df_xtrain = None
        self.df_ytrain = None
        self.df_xtest = None
        self.df_ytest = None
        #selections
        self.s_selbkgml = data_param["ml"]["sel_bkgml"]
        self.s_selsigml = data_param["ml"]["sel_sigml"]
        self.p_equalise_sig_bkg = data_param["ml"].get("equalise_sig_bkg", False)
        #model param
        self.db_model = model_config
        self.p_class = None
        self.p_classname = None
        self.p_trainedmod = None
        self.s_suffix = None

        #significance
        self.is_fonll_from_root = data_param["ml"]["opt"]["isFONLLfromROOT"]
        self.f_fonll = data_param["ml"]["opt"]["filename_fonll"]
        if self.is_fonll_from_root and "fonll_particle" not in data_param["ml"]["opt"]:
            self.logger.fatal("Attempt to read FONLL from ROOT file but field " \
                    "\"fonll_particle\" not provided in database")
        self.p_fonllparticle = data_param["ml"]["opt"].get("fonll_particle", "")
        self.p_fonllband = data_param["ml"]["opt"]["fonll_pred"]
        self.p_fragf = data_param["ml"]["opt"]["FF"]
        self.p_sigmamb = data_param["ml"]["opt"]["sigma_MB"]
        self.p_taa = data_param["ml"]["opt"]["Taa"]
        self.p_br = data_param["ml"]["opt"]["BR"]
        self.p_fprompt = data_param["ml"]["opt"]["f_prompt"]
        self.p_bkgfracopt = data_param["ml"]["opt"]["bkg_data_fraction"]
        self.p_nstepsign = data_param["ml"]["opt"]["num_steps"]
        self.p_bkg_func = data_param["ml"]["opt"]["bkg_function"]
        self.p_savefit = data_param["ml"]["opt"]["save_fit"]
        self.p_nevtml = None
        self.p_nevttot = None
        self.p_presel_gen_eff = data_param["ml"]["opt"]["presel_gen_eff"]
        # Potentially mask certain values (e.g. nsigma TOF of -999)
        self.p_mask_values = data_param["ml"].get("mask_values", None)
        self.p_mass_fit_lim = data_param["analysis"][self.p_typean]['mass_fit_lim']
        self.p_bin_width = data_param["analysis"][self.p_typean]['bin_width']
        self.p_num_bins = int(round((self.p_mass_fit_lim[1] - self.p_mass_fit_lim[0]) / \
                                     self.p_bin_width))
        self.p_mass = data_param["mass"]
        self.p_raahp = raahp
        self.create_suffix()
        self.preparesample()
        self.loadmodels()
        self.df_evt_data = None
        self.df_evttotsample_data = None

        self.f_reco_applieddata = \
                self.f_reco_applieddata.replace(".pkl", "%s.pkl" % self.s_suffix)
        self.f_reco_appliedmc = \
                self.f_reco_appliedmc.replace(".pkl", "%s.pkl" % self.s_suffix)
        self.f_df_ml_test_to_df = f"{self.dirmlout}/testsample_{self.s_suffix}_mldecision.pkl"
        self.f_mltest_applied = f"{self.dirmlout}/testsample_{self.s_suffix}_mldecision.pkl"
        self.df_mltest_applied = None

        print(training_var)

    def create_suffix(self):
        string_selection = createstringselection(self.v_bin, self.p_binmin, self.p_binmax)
        self.s_suffix = f"{self.p_case}_{string_selection}"

    def prepare_data_mc_mcgen(self):

        self.logger.info("Prepare data reco as well as MC reco and gen")
        if os.path.exists(self.f_reco_applieddata) \
                and os.path.exists(self.f_reco_appliedmc) \
                and self.step_done("preparemlsamples_data_mc_mcgen"):
            self.df_data = pickle.load(openfile(self.f_reco_applieddata, "rb"))
            self.df_mc = pickle.load(openfile(self.f_reco_appliedmc, "rb"))
        else:
            self.df_data = pickle.load(openfile(self.f_reco_data, "rb"))
            self.df_mc = pickle.load(openfile(self.f_reco_mc, "rb"))
            self.df_data = selectdfquery(self.df_data, self.p_evtsel)
            self.df_mc = selectdfquery(self.df_mc, self.p_evtsel)

            self.df_data = selectdfquery(self.df_data, self.p_triggersel_data)
            self.df_mc = selectdfquery(self.df_mc, self.p_triggersel_mc)

        self.df_mcgen = pickle.load(openfile(self.f_gen_mc, "rb"))
        self.df_mcgen = selectdfquery(self.df_mcgen, self.p_evtsel)
        self.df_mcgen = selectdfquery(self.df_mcgen, self.p_triggersel_mc)
        self.df_mcgen = self.df_mcgen.query(self.p_presel_gen_eff)

        self.arraydf = [self.df_data, self.df_mc]
        self.df_mc = seldf_singlevar(self.df_mc, self.v_bin, self.p_binmin, self.p_binmax)
        self.df_mcgen = seldf_singlevar(self.df_mcgen, self.v_bin, self.p_binmin, self.p_binmax)
        self.df_data = seldf_singlevar(self.df_data, self.v_bin, self.p_binmin, self.p_binmax)


    def preparesample(self):

        self.logger.info("Prepare Sample")

        filename_train = \
                os.path.join(self.dirmlout, f"df_train_{self.p_binmin}_{self.p_binmax}.pkl")
        #filename_test = \
        #        os.path.join(self.dirmlout, f"df_test_{self.p_binmin}_{self.p_binmax}.pkl")
        filename_test = \
                os.path.join(self.dirmlout, f"testsample_LcpKpi_dfselection_pt_cand_{self.p_binmin}.0_{self.p_binmax}.0_mldecision.pkl")

        if os.path.exists(filename_train) \
                and os.path.exists(filename_test) \
                and self.step_done("preparemlsamples"):
            self.df_mltrain = pickle.load(openfile(filename_train, "rb"))
            self.df_mltest = pickle.load(openfile(filename_test, "rb"))

        else:

            self.prepare_data_mc_mcgen()

            self.df_sig, self.df_bkg = self.arraydf[self.p_tagsig], self.arraydf[self.p_tagbkg]
            self.df_sig = seldf_singlevar(self.df_sig, self.v_bin, self.p_binmin, self.p_binmax)
            self.df_bkg = seldf_singlevar(self.df_bkg, self.v_bin, self.p_binmin, self.p_binmax)
            self.df_sig = self.df_sig.query(self.s_selsigml)
            self.df_bkg = self.df_bkg.query(self.s_selbkgml)
            self.df_bkg["ismcsignal"] = 0
            self.df_bkg["ismcprompt"] = 0
            self.df_bkg["ismcfd"] = 0
            self.df_bkg["ismcbkg"] = 0

            if self.p_equalise_sig_bkg:
                self.p_nsig = min(len(self.df_sig), len(self.df_bkg), self.p_nsig)
                self.p_nbkg = min(len(self.df_sig), len(self.df_bkg), self.p_nbkg)

            self.df_ml = pd.DataFrame()
            self.df_sig = shuffle(self.df_sig, random_state=self.rnd_shuffle)
            self.df_bkg = shuffle(self.df_bkg, random_state=self.rnd_shuffle)
            self.df_sig = self.df_sig[:self.p_nsig]
            self.df_bkg = self.df_bkg[:self.p_nbkg]
            self.df_sig[self.v_sig] = 1
            self.df_bkg[self.v_sig] = 0
            self.df_ml = pd.concat([self.df_sig, self.df_bkg])
            self.df_mltrain, self.df_mltest = train_test_split(self.df_ml, \
                                               test_size=self.test_frac, random_state=self.rnd_splt)
            self.df_mltrain = self.df_mltrain.reset_index(drop=True)
            self.df_mltest = self.df_mltest.reset_index(drop=True)

            # Write for later usage
            pickle.dump(self.df_mltrain, openfile(filename_train, "wb"), protocol=4)
            pickle.dump(self.df_mltest, openfile(filename_test, "wb"), protocol=4)

        # Now continue with extracting signal and background stats and report
        self.df_sigtrain, self.df_bkgtrain = split_df_sigbkg(self.df_mltrain, self.v_sig)
        self.df_sigtest, self.df_bkgtest = split_df_sigbkg(self.df_mltest, self.v_sig)
        self.logger.info("Total number of candidates: train %d and test %d", len(self.df_mltrain),
                         len(self.df_mltest))
        self.logger.info("Number of signal candidates: train %d and test %d",
                         len(self.df_sigtrain), len(self.df_sigtest))
        self.logger.info("Number of bkg candidates: %d and test %d", len(self.df_bkgtrain),
                         len(self.df_bkgtest))

        self.logger.info("Aim for number of signal events: %d", self.p_nsig)
        self.logger.info("Aim for number of background events: %d", self.p_nbkg)

        if self.p_nsig > (len(self.df_sigtrain) + len(self.df_sigtest)):
            self.logger.warning("There are not enough signal events")
        if self.p_nbkg > (len(self.df_bkgtrain) + len(self.df_bkgtest)):
            self.logger.warning("There are not enough background events")

        if self.p_mask_values:
            self.logger.info("Maksing values for training and testing")
            mask_df(self.df_mltrain, self.p_mask_values)
            mask_df(self.df_mltest, self.p_mask_values)
        # Final preparation of signal and background samples for training and testing
        self.df_xtrain = self.df_mltrain[self.v_train]
        self.df_ytrain = self.df_mltrain[self.v_sig]
        self.df_xtest = self.df_mltest[self.v_train]
        self.df_ytest = self.df_mltest[self.v_sig]

        self.step_done("preparemlsamples")

    def step_done(self, step):
        if self.steps_done is None:
            self.steps_done = []

        step_name = f"{step}_{self.p_binmin}_{self.p_binmax}"
        if step_name in self.steps_done:
            print("\n\n")
            self.logger.warning("Done ML step %s already. It's skipped now. Remove the step " \
                    "from the list in the following file", step_name)
            print(self.file_steps_done)
            print("\n\n")
            return True

        # Add this steps and update the corresponsing file
        self.steps_done.append(step_name)
        dump_yaml_from_dict({"done": self.steps_done}, self.file_steps_done)

        return False


    def do_corr(self):
        if self.step_done("distributions_correlations"):
            return

        self.logger.info("Make feature distributions and correlation plots")


        def make_plot_name(output, label, n_var, binmin, binmax):
            return f'{output}/CorrMatrix_{label}_nVar{n_var}_{binmin:.1f}_{binmax:.1f}.png'


        vardistplot(self.df_sigtrain, self.df_bkgtrain,
                    self.v_all, self.dirmlplot,
                    self.p_binmin, self.p_binmax, self.p_plot_options)

        if self.v_selected:
            vardistplot(self.df_sigtrain, self.df_bkgtrain,
                        self.v_selected, self.dirmlplot,
                        self.p_binmin, self.p_binmax, self.p_plot_options)

        vardistplot(self.df_sigtrain, self.df_bkgtrain,
                    self.v_train, self.dirmlplot,
                    self.p_binmin, self.p_binmax, self.p_plot_options)

        scatterplot(self.df_sigtrain, self.df_bkgtrain,
                    self.v_corrx, self.v_corry,
                    self.dirmlplot, self.p_binmin, self.p_binmax)

        output = make_plot_name(self.dirmlplot, "Signal_all_vars", len(self.v_all),
                                self.p_binmin, self.p_binmax)
        correlationmatrix(self.df_sigtrain, self.v_all, "Signal", output,
                          self.p_binmin, self.p_binmax, self.p_plot_options)

        output = make_plot_name(self.dirmlplot, "Background_all_vars", len(self.v_all),
                                self.p_binmin, self.p_binmax)
        correlationmatrix(self.df_bkgtrain, self.v_all, "Background", output,
                          self.p_binmin, self.p_binmax, self.p_plot_options)

        if self.v_selected:
            output = make_plot_name(self.dirmlplot, "Signal_selected_vars", len(self.v_selected),
                                    self.p_binmin, self.p_binmax)
            correlationmatrix(self.df_sigtrain, self.v_selected, "Signal", output,
                              self.p_binmin, self.p_binmax, self.p_plot_options)

            output = make_plot_name(self.dirmlplot, "Background_selected_vars",
                                    len(self.v_selected), self.p_binmin, self.p_binmax)
            correlationmatrix(self.df_bkgtrain, self.v_selected, "Background", output,
                              self.p_binmin, self.p_binmax, self.p_plot_options)

        output = make_plot_name(self.dirmlplot, "Signal_features", len(self.v_train),
                                self.p_binmin, self.p_binmax)
        correlationmatrix(self.df_sigtrain, self.v_train, "Signal", output,
                          self.p_binmin, self.p_binmax, self.p_plot_options)

        output = make_plot_name(self.dirmlplot, "Background_features", len(self.v_train),
                                self.p_binmin, self.p_binmax)
        correlationmatrix(self.df_bkgtrain, self.v_train, "Background", output,
                          self.p_binmin, self.p_binmax, self.p_plot_options)

        # For each plot there was an IO returned. That is not needed and was only important for
        # browser interface version of this package which became completely deprecated

        #return imageIO_vardist_all, imageIO_vardist_train, imageIO_scatterplot, \
        #          imageIO_corr_sig_all, imageIO_corr_bkg_all, imageIO_corr_sig_train, \
        #          imageIO_corr_bkg_train

    def loadmodels(self):
        classifiers_scikit, names_scikit, _, _ = getclf_scikit(self.db_model)
        classifiers_xgboost, names_xgboost, _, _ = getclf_xgboost(self.db_model)
        classifiers_keras, names_keras, _, _ = getclf_keras(self.db_model,
                                                            len(self.df_xtrain.columns))
        self.p_class = classifiers_scikit+classifiers_xgboost+classifiers_keras
        self.p_classname = names_scikit+names_xgboost+names_keras


        # Try to read trained models
        clfs = readmodels(self.p_classname, self.dirmlout, self.s_suffix)
        if clfs:
            self.logger.info("Read and use models from disk. Remove them if you don't want to " \
                    "use them")
            self.p_trainedmod = clfs
            self.p_class = clfs
            return

    def do_train(self):
        if self.step_done("training"):
            return

        self.logger.info("Training")
        t0 = time.time()
        self.p_trainedmod = fit(self.p_classname, self.p_class, self.df_xtrain, self.df_ytrain)
        savemodels(self.p_classname, self.p_trainedmod, self.dirmlout, self.s_suffix)
        self.logger.info("Training over")
        self.logger.info("Time elapsed = %.3f", time.time() - t0)

    def do_test(self):

        self.do_train()
        if self.step_done("test"):
            self.df_mltest_applied = pickle.load(openfile(self.f_mltest_applied, "rb"))
            return

        self.logger.info("Testing")
        self.df_mltest_applied = test(self.p_mltype, self.p_classname, self.p_trainedmod,
                                      self.df_mltest, self.v_train, self.v_sig)
        df_ml_test_to_root = self.dirmlout+"/testsample_%s_mldecision.root" % (self.s_suffix)
        pickle.dump(self.df_mltest_applied, openfile(self.f_mltest_applied, "wb"), protocol=4)
        write_tree(df_ml_test_to_root, self.n_treetest, self.df_mltest_applied)

    def do_apply(self):

        self.prepare_data_mc_mcgen()

        if self.step_done("application"):
            return

        self.do_train()

        self.logger.info("Application")

        df_data = apply(self.p_mltype, self.p_classname, self.p_trainedmod,
                        self.df_data, self.v_train)
        df_mc = apply(self.p_mltype, self.p_classname, self.p_trainedmod,
                      self.df_mc, self.v_train)
        pickle.dump(df_data, openfile(self.f_reco_applieddata, "wb"), protocol=4)
        pickle.dump(df_mc, openfile(self.f_reco_appliedmc, "wb"), protocol=4)

    def do_crossval(self):
        if self.step_done("cross_validation"):
            return
        self.logger.info("Do cross validation")
        df_scores = cross_validation_mse(self.p_classname, self.p_class,
                                         self.df_xtrain, self.df_ytrain,
                                         self.p_nkfolds, self.p_ncorescross)
        plot_cross_validation_mse(self.p_classname, df_scores, self.s_suffix, self.dirmlplot)

    def do_learningcurve(self):
        if self.step_done("leaningcurve"):
            return
        self.logger.info("Make learning curve")
        npoints = 10
        plot_learning_curves(self.p_classname, self.p_class, self.s_suffix,
                             self.dirmlplot, self.df_xtrain, self.df_ytrain, npoints)

    def do_roc(self):
        if self.step_done("roc_simple"):
            return

        self.do_train()

        self.logger.info("Make ROC for train")
        precision_recall(self.p_classname, self.p_class, self.s_suffix,
                         self.df_xtrain, self.df_ytrain, self.p_nkfolds, self.dirmlplot)

    def do_roc_train_test(self):
        if self.step_done("roc_train_test"):
            return

        self.do_train()

        self.logger.info("Make ROC for train and test")
        roc_train_test(self.p_classname, self.p_class, self.df_xtrain, self.df_ytrain,
                       self.df_xtest, self.df_ytest, self.s_suffix, self.dirmlplot,
                       self.p_binmin, self.p_binmax)

    def do_plot_model_pred(self):
        if self.step_done("plot_model_pred"):
            return

        self.do_train()

        self.logger.info("Plot model prediction distribution")
        plot_overtraining(self.p_classname, self.p_class, self.s_suffix, self.dirmlplot,
                          self.df_xtrain, self.df_ytrain, self.df_xtest, self.df_ytest)

    def do_importance(self):
        if self.step_done("importance"):
            return

        self.do_train()

        self.logger.info("Do simple importance")
        importanceplotall(self.v_train, self.p_classname, self.p_class,
                          self.s_suffix, self.dirmlplot)

    def do_importance_shap(self):
        if self.step_done("importance_shap"):
            return

        self.do_train()

        self.logger.info("Do SHAP importance")
        shap_study(self.p_classname, self.p_class, self.df_xtrain, self.s_suffix, self.dirmlplot,
                   self.p_plot_options)

    def do_bayesian_opt(self):
        if self.step_done("bayesian_opt"):
            return
        self.logger.info("Do Bayesian optimisation for all classifiers")
        _, names_scikit, _, bayes_opt_scikit = getclf_scikit(self.db_model)
        _, names_xgboost, _, bayes_opt_xgboost = getclf_xgboost(self.db_model)
        _, names_keras, _, bayes_opt_keras = getclf_keras(self.db_model,
                                                          len(self.df_xtrain.columns))
        clfs_all = bayes_opt_scikit + bayes_opt_xgboost + bayes_opt_keras
        clfs_names_all = names_scikit + names_xgboost + names_keras


        clfs_names_all = [name for name, clf in zip(clfs_names_all, clfs_all) if clf]
        clfs_all = [clf for clf in clfs_all if clf]

        out_dirs = [os.path.join(self.dirmlplot, "bayesian_opt", name, f"{name}{self.s_suffix}") \
                for name in clfs_names_all]
        checkmakedirlist(out_dirs)

        # Now, do it
        for opt, out_dir in zip(clfs_all, out_dirs):
            opt.x_train = self.df_xtrain
            opt.y_train = self.df_ytrain

            opt.optimise(ncores=self.p_ncorescross)
            opt.save(out_dir)
            opt.plot(out_dir)


    def do_grid(self):
        if self.step_done("grid"):
            return
        self.logger.info("Do grid search")
        clfs_scikit, names_scikit, grid_params_scikit, _ = getclf_scikit(self.db_model)
        clfs_xgboost, names_xgboost, grid_params_xgboost, _ = getclf_xgboost(self.db_model)
        clfs_keras, names_keras, grid_params_keras, _ = getclf_keras(self.db_model,
                                                                     len(self.df_xtrain.columns))
        clfs_grid_params_all = grid_params_scikit + grid_params_xgboost + grid_params_keras
        clfs_all = clfs_scikit + clfs_xgboost + clfs_keras
        clfs_names_all = names_scikit + names_xgboost + names_keras

        clfs_all = [clf for clf, gps in zip(clfs_all, clfs_grid_params_all) if gps]
        clfs_names_all = [name for name, gps in zip(clfs_names_all, clfs_grid_params_all) if gps]
        clfs_grid_params_all = [gps for gps in clfs_grid_params_all if gps]

        out_dirs = [os.path.join(self.dirmlplot, "grid_search", name, f"{name}{self.s_suffix}") \
                for name in clfs_names_all]
        if checkdirlist(out_dirs):
            # Only draw results if any can be found
            self.logger.warning("Not overwriting anything, just plotting again what was done " \
                    "before and returning. Please remove corresponding directories " \
                    "if you are certain you want do do grid search again")
            perform_plot_gridsearch(clfs_names_all, out_dirs)
            return
        checkmakedirlist(out_dirs)

        do_gridsearch(clfs_names_all, clfs_all, clfs_grid_params_all, self.df_xtrain,
                      self.df_ytrain, self.p_nkfolds, out_dirs, self.p_ncorescross)
        perform_plot_gridsearch(clfs_names_all, out_dirs)

    def do_boundary(self):
        if self.step_done("boundary"):
            return
        classifiers_scikit_2var, names_2var = getclf_scikit(self.db_model)
        classifiers_keras_2var, names_keras_2var = getclf_keras(self.db_model, 2)
        classifiers_2var = classifiers_scikit_2var+classifiers_keras_2var
        names_2var = names_2var+names_keras_2var
        x_test_boundary = self.df_xtest[self.v_bound]
        trainedmodels_2var = fit(names_2var, classifiers_2var, x_test_boundary, self.df_ytest)
        decisionboundaries(
            names_2var, trainedmodels_2var, self.s_suffix+"2var", x_test_boundary,
            self.df_ytest, self.dirmlplot)

    def do_efficiency(self):
        if self.step_done("efficiency"):
            return

        self.do_test()

        self.logger.info("Doing efficiency estimation")
        fig_eff = plt.figure(figsize=(20, 15))
        plt.xlabel('Threshold', fontsize=20)
        plt.ylabel('Model Efficiency', fontsize=20)
        plt.title("Efficiency vs Threshold", fontsize=20)
        df_sig = self.df_mltest[self.df_mltest["ismcprompt"] == 1]
        for name in self.p_classname:
            eff_array, eff_err_array, x_axis = calc_sigeff_steps(self.p_nstepsign, df_sig, name)
            plt.figure(fig_eff.number)
            plt.errorbar(x_axis, eff_array, yerr=eff_err_array, alpha=0.3, label=f'{name}',
                         elinewidth=2.5, linewidth=4.0)
        plt.figure(fig_eff.number)
        plt.legend(loc="lower left", prop={'size': 18})
        plt.savefig(f'{self.dirmlplot}/Efficiency_{self.s_suffix}.png')
        with open(f'{self.dirmlplot}/Efficiency_{self.s_suffix}.pickle', 'wb') as out:
            pickle.dump(fig_eff, out)

    #pylint: disable=too-many-locals
    def do_significance(self):
        if self.step_done("significance"):
            return

        self.do_apply()
        self.do_test()

        self.logger.info("Doing significance optimization")
        gROOT.SetBatch(True)
        gROOT.ProcessLine("gErrorIgnoreLevel = kWarning;")
        #first extract the number of data events in the ml sample
        # This might need a revisit, for now just extract the numbers from the ML merged
        # event count (aka from a YAML since the actual events are not needed)
        # Before the ML count was always taken from the ML merged event df while the total
        # number was taken from the event counter. But the latter is basically not used
        # anymore for a long time cause "dofullevtmerge" is mostly "false" in the DBs
        #and the total number of events
        count_dict = parse_yaml(self.f_evt_count_ml)
        self.p_nevttot = count_dict["evtorig"]
        self.p_nevtml = count_dict["evt"]
        self.logger.info("Number of data events used for ML: %d", self.p_nevtml)
        self.logger.info("Total number of data events: %d", self.p_nevttot)
        #calculate acceptance correction. we use in this case all
        #the signal from the mc sample, without limiting to the n. signal
        #events used for training
        #denacc = len(self.df_mcgen[self.df_mcgen["ismcprompt"] == 1])
        numacc = len(self.df_mc[self.df_mc["ismcprompt"] == 1])
        print(list(self.df_mcgen))
        denacc = len(self.df_mcgen[(self.df_mcgen["ismcprompt"] == 1) & (self.df_mcgen["dau_in_acc"]==1)])
        numacc = len(self.df_mcgen[(self.df_mcgen["ismcprompt"] == 1) & (self.df_mcgen["y_cand"] > -0.5) & (self.df_mcgen["y_cand"] < 0.5)])
        print("den",denacc)
        print("num",numacc)
        acc, acc_err = calc_eff(numacc, denacc)
        self.logger.info("Acceptance: %.3e +/- %.3e", acc, acc_err)
        #calculation of the expected fonll signals
        delta_pt = self.p_binmax - self.p_binmin
        if self.is_fonll_from_root:
            df_fonll = TFile.Open(self.f_fonll)
            df_fonll_Lc = df_fonll.Get(self.p_fonllparticle+"_"+self.p_fonllband)
            bin_min = df_fonll_Lc.FindBin(self.p_binmin)
            bin_max = df_fonll_Lc.FindBin(self.p_binmax)
            prod_cross = df_fonll_Lc.Integral(bin_min, bin_max)* self.p_fragf * 1e-12 / delta_pt
            signal_yield = 2. * prod_cross * delta_pt * acc * self.p_taa * self.p_br \
                           / (self.p_sigmamb * self.p_fprompt)
            #now we plot the fonll expectation
            cFONLL = TCanvas('cFONLL', 'The FONLL expectation')
            df_fonll_Lc.GetXaxis().SetRangeUser(0, 16)
            df_fonll_Lc.Draw("")
            cFONLL.SaveAs("%s/FONLL_curve_%s.png" % (self.dirmlplot, self.s_suffix))
        else:
            df_fonll = pd.read_csv(self.f_fonll)
            df_fonll_in_pt = \
                    df_fonll.query('(pt >= @self.p_binmin) and (pt < @self.p_binmax)')\
                    [self.p_fonllband]
            prod_cross = df_fonll_in_pt.sum() * self.p_fragf * 1e-12 / delta_pt
            print("prod_cross:")
            print(prod_cross)
            print("delta_pt:")
            print(delta_pt)
            print("acc:")
            print(acc)
            print("p_taa:")
            print(self.p_taa)
            print("p_br:")
            print(self.p_br)
            print("p_sigmamb:")
            print(self.p_sigmamb)
            print("p_fprompt:")
            print(self.p_fprompt)
            print("p_raahp:")
            print(self.p_raahp)

            f = TFile.Open('/home/cbartels/Scripts/CrossSection/LcpK0sCorrectedYieldPerEvent_010_3050.root', 'read')
            pK0s = f.Get('hAAC_1')
            test = pK0s.GetArray()
            meascross = test[5]
            #test.SetSize(pK0s.GetNbinsX())
            #test = np.array(test)
            print("binmin")
            print(self.p_binmin)

            if self.p_binmin == 4:
                meascross = test[2]
            elif self.p_binmin == 6:
                meascross = test[3]
            elif self.p_binmin == 8: 
                meascross = test[4]
            elif self.p_binmin == 12:
                meascross = test[5]
            else :
                meascross = prod_cross
                print("ERROR: NO cross section found")
            print("new thing:")
            print(meascross)
            print("old thing:")
            print(prod_cross)
            signal_yield = 2. * meascross * delta_pt * self.p_br * acc \
                           / (self.p_sigmamb * self.p_fprompt)
            #signal_yield = 2. * prod_cross * delta_pt * self.p_br * acc * self.p_taa \
            #               / (self.p_sigmamb * self.p_fprompt)
            #now we plot the fonll expectation
            fig = plt.figure(figsize=(20, 15))
            plt.subplot(111)
            plt.plot(df_fonll['pt'], df_fonll[self.p_fonllband] * self.p_fragf, linewidth=4.0)
            plt.xlabel('P_t [GeV/c]', fontsize=20)
            plt.ylabel('Cross Section [pb/GeV]', fontsize=20)
            plt.title("FONLL cross section " + self.p_case, fontsize=20)
            plt.semilogy()
            plt.savefig(f'{self.dirmlplot}/FONLL_curve_{self.s_suffix}.png')
            plt.close(fig)

        self.logger.info("Expected signal yield: %.3e", signal_yield)
        signal_yield = self.p_raahp * signal_yield
        self.logger.info("Expected signal yield x RAA hp: %.3e", signal_yield)

        df_data_sideband = self.df_data.query(self.s_selbkgml)
        #print(df_data_sideband["inv_mass"])
        df_data_sideband = shuffle(df_data_sideband, random_state=self.rnd_shuffle)
        df_data_sideband = df_data_sideband.tail(round(len(df_data_sideband) * self.p_bkgfracopt))
        hmass = TH1F('hmass', '', self.p_num_bins, self.p_mass_fit_lim[0], self.p_mass_fit_lim[1])
        df_mc_signal = self.df_mc[self.df_mc["ismcsignal"] == 1]
        mass_array = df_mc_signal[self.v_invmass].values
        for mass_value in np.nditer(mass_array):
            hmass.Fill(mass_value)

        gaus_fit = TF1("gaus_fit", "gaus", self.p_mass_fit_lim[0], self.p_mass_fit_lim[1])
        gaus_fit.SetParameters(0, hmass.Integral())
        gaus_fit.SetParameters(1, self.p_mass)
        gaus_fit.SetParameters(2, 0.02)
        self.logger.info("To fit the signal a gaussian function is used")
        fitsucc = hmass.Fit("gaus_fit", "RQ")

        if int(fitsucc) != 0:
            self.logger.warning("Problem in signal peak fit")
            sigma = 0.

        sigma = gaus_fit.GetParameter(2)
        self.logger.info("Mean of the gaussian: %.3e", gaus_fit.GetParameter(1))
        self.logger.info("Sigma of the gaussian: %.3e", sigma)
        sig_region = [self.p_mass - 3 * sigma, self.p_mass + 3 * sigma]
        fig_signif_pevt = plt.figure(figsize=(20, 15))
        plt.xlabel('Threshold', fontsize=20)
        plt.ylabel(r'Significance Per Event ($3 \sigma$)', fontsize=20)
        #plt.title("Significance Per Event vs Threshold", fontsize=20)
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)
        fig_signif = plt.figure(figsize=(20, 15))
        plt.xlabel('Threshold', fontsize=20)
        plt.ylabel(r'Significance ($3 \sigma$)', fontsize=20)
        #plt.title("Significance vs Threshold", fontsize=20)
        plt.xticks(fontsize=18)
        plt.yticks(fontsize=18)

        df_sig = self.df_mltest[self.df_mltest["ismcprompt"] == 1]

        for name in self.p_classname:
            eff_array, eff_err_array, x_axis = calc_sigeff_steps(self.p_nstepsign, df_sig, name)
            bkg_array, bkg_err_array, _ = calc_bkg(df_data_sideband, name, self.p_nstepsign,
                                                   self.p_mass_fit_lim, self.p_bkg_func,
                                                   self.p_bin_width, sig_region, self.p_savefit,
                                                   self.dirmlplot, [self.p_binmin, self.p_binmax],
                                                   self.v_invmass)
            sig_array = [eff * signal_yield for eff in eff_array]
            sig_err_array = [eff_err * signal_yield for eff_err in eff_err_array]
            print("sig_array:")
            print(sig_array)
            print("bkg_array first:")
            print(bkg_array)
            print("p_bkgfracopt:")
            print(self.p_bkgfracopt)
            print("p_nevtml:")
            print(self.p_nevtml)
            bkg_array = [bkg / (self.p_bkgfracopt * self.p_nevtml) for bkg in bkg_array]
            print("bkg_array:")
            print(bkg_array)
            bkg_err_array = [bkg_err / (self.p_bkgfracopt * self.p_nevtml) \
                             for bkg_err in bkg_err_array]
            signif_array, signif_err_array = calc_signif(sig_array, sig_err_array, bkg_array,
                                                         bkg_err_array)
            #print("eff: bkg: signif:")
            #for x in range(len(signif_array)):
            #    print(eff_array[x]," ",bkg_array[x]," ",signif_array[x])
            plt.figure(fig_signif_pevt.number)
            plt.ylim(-1,10)
            plt.errorbar(x_axis, signif_array, yerr=signif_err_array, label=f'{name}',
                         elinewidth=2.5, linewidth=5.0)
            signif_array_ml = [sig * sqrt(self.p_nevtml) for sig in signif_array]
            signif_err_array_ml = [sig_err * sqrt(self.p_nevtml) for sig_err in signif_err_array]
            plt.figure(fig_signif.number)
            plt.errorbar(x_axis, signif_array_ml, yerr=signif_err_array_ml,
                         label=f'{name}_ML_dataset', elinewidth=2.5, linewidth=5.0)
            plt.text(0.7, 0.95,
                     f" ${self.p_binmin} < p_\\mathrm{{T}}/(\\mathrm{{GeV}}/c) < {self.p_binmax}$",
                     verticalalignment="center", transform=fig_signif.gca().transAxes, fontsize=30)
            #signif_array_tot = [sig * sqrt(self.p_nevttot) for sig in signif_array]
            #signif_err_array_tot = [sig_err * sqrt(self.p_nevttot) for sig_err in signif_err_array]
            #plt.figure(fig_signif.number)
            #plt.errorbar(x_axis, signif_array_tot, yerr=signif_err_array_tot,
            #             label=f'{name}_Tot', elinewidth=2.5, linewidth=5.0)
            #plt.ylim(-1,10)
            plt.figure(fig_signif_pevt.number)
            plt.legend(loc="upper left", prop={'size': 30})
            plt.savefig(f'{self.dirmlplot}/Significance_PerEvent_{self.s_suffix}.png')
            plt.figure(fig_signif.number)
            plt.legend(loc="upper left", prop={'size': 30})
            mpl.rcParams.update({"text.usetex": True})
            plt.savefig(f'{self.dirmlplot}/Significance_{self.s_suffix}.png')
            mpl.rcParams.update({"text.usetex": False})
            #plt.ylim(-1,10)

            with open(f'{self.dirmlplot}/Significance_{self.s_suffix}.pickle', 'wb') as out:
                pickle.dump(fig_signif, out)

            plt.close(fig_signif_pevt)
            plt.close(fig_signif)

    def do_scancuts(self):
        if self.step_done("scancuts"):
            return
        self.logger.info("Scanning cuts")

        self.do_apply()

        prob_array = [0.0, 0.2, 0.6, 0.9]
        dfdata = pickle.load(openfile(self.f_reco_applieddata, "rb"))
        dfmc = pickle.load(openfile(self.f_reco_appliedmc, "rb"))
        vardistplot_probscan(dfmc, self.v_all, "xgboost_classifier",
                             prob_array, self.dirmlplot, "mc" + self.s_suffix,
                             0, self.p_plot_options)
        vardistplot_probscan(dfmc, self.v_all, "xgboost_classifier",
                             prob_array, self.dirmlplot, "mc" + self.s_suffix,
                             1, self.p_plot_options)
        vardistplot_probscan(dfdata, self.v_all, "xgboost_classifier",
                             prob_array, self.dirmlplot, "data" + self.s_suffix,
                             0, self.p_plot_options)
        vardistplot_probscan(dfdata, self.v_all, "xgboost_classifier",
                             prob_array, self.dirmlplot, "data" + self.s_suffix,
                             1, self.p_plot_options)
        if not self.v_cuts:
            self.logger.warning("No variables for cut efficiency scan. Will be skipped")
            return
        efficiency_cutscan(dfmc, self.v_cuts, "xgboost_classifier", 0.0,
                           self.dirmlplot, "mc" + self.s_suffix, self.p_plot_options)
        efficiency_cutscan(dfmc, self.v_cuts, "xgboost_classifier", 0.5,
                           self.dirmlplot, "mc" + self.s_suffix, self.p_plot_options)
        efficiency_cutscan(dfdata, self.v_cuts, "xgboost_classifier", 0.0,
                           self.dirmlplot, "data" + self.s_suffix, self.p_plot_options)
        efficiency_cutscan(dfdata, self.v_cuts, "xgboost_classifier", 0.5,
                           self.dirmlplot, "data" + self.s_suffix, self.p_plot_options)
