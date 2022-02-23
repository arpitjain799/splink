import logging
from copy import copy, deepcopy
from statistics import median

from .blocking import block_using_rules
from .comparison_vector_values import compute_comparison_vector_values
from .em_training import EMTrainingSession
from .misc import bayes_factor_to_prob, escape_columns, prob_to_bayes_factor
from .predict import predict
from .settings import Settings
from .term_frequencies import (
    colname_to_tf_tablename,
    join_tf_to_input_df,
    term_frequencies_dict,
)
from .m_training import estimate_m_values_from_label_column
from .u_training import estimate_u_values

from .vertically_concatenate import vertically_concatente

logger = logging.getLogger(__name__)


class SplinkDataFrame:
    """Abstraction over dataframe to handle basic operations
    like retrieving columns, which need different implementations
    depending on whether it's a spark dataframe, sqlite table etc.
    """

    def __init__(self, df_name, df_value):
        self.df_name = df_name
        self.df_value = df_value

    @property
    def columns(self):
        pass

    @property
    def columns_escaped(self):
        cols = self.columns
        return escape_columns(cols)

    def validate():
        pass

    def random_sample_sql(percent):
        pass

    def as_record_dict(self):
        pass


class Linker:
    def __init__(self, settings_dict, input_tables, tf_tables={}):
        self.settings_dict = settings_dict

        self.settings_obj = Settings(settings_dict)
        self.input_dfs = self._get_input_dataframe_dict(input_tables)
        self.input_tf_tables = self._get_input_tf_dict(tf_tables)
        self._validate_input_dfs()
        self.em_training_sessions = []

        df_dict = vertically_concatente(self.input_dfs, self.execute_sql)
        df_dict = self._add_term_frequencies(df_dict, False)
        self.input_dfs = {**self.input_dfs, **df_dict}

    def __deepcopy__(self, memo):
        new_linker = copy(self)
        new_linker.em_training_sessions = []
        new_settings = deepcopy(self.settings_obj)
        new_linker.settings_obj = new_settings
        return new_linker

    def _get_input_dataframe_dict(self, df_dict):
        d = {}
        for df_name, df_value in df_dict.items():
            d[df_name] = self._df_as_obj(df_name, df_value)
        return d

    def _get_input_tf_dict(self, df_dict):
        d = {}
        for df_name, df_value in df_dict.items():
            renamed = colname_to_tf_tablename(df_name)
            d[renamed] = self._df_as_obj(renamed, df_value)
        return d

    def execute_sql(sql, df_dict, output_table_name):
        pass

    def _validate_input_dfs(self):
        for df in self.input_dfs.values():
            df.validate()

    def deterministic_link(self, return_df_as_value=True):

        df_dict = block_using_rules(self.settings_obj, self.input_dfs, self.execute_sql)
        if return_df_as_value:
            return df_dict["__splink__df_blocked"].df_value
        else:
            return df_dict

    def _blocked_comparisons(self, return_df_as_value=True):

        df_dict = block_using_rules(self.settings_obj, self.input_dfs, self.execute_sql)
        return df_dict

    def _add_term_frequencies(self, df_dict, return_df_as_value=True):

        if not self.settings_obj._term_frequency_columns:
            sql = "select * from __splink__df_concat"
            return self.execute_sql(sql, df_dict, "__splink__df_concat_with_tf")

        # Want to return tf tables as a dict of tables.
        tf_dict = term_frequencies_dict(
            self.settings_obj, df_dict, self.input_tf_tables, self.execute_sql
        )

        df_dict = {**df_dict, **tf_dict}

        df_dict = join_tf_to_input_df(self.settings_obj, df_dict, self.execute_sql)
        if return_df_as_value:
            return df_dict["__splink__df_concat_with_tf"].df_value
        else:
            return df_dict

    def comparison_vectors(self, return_df_as_value=True):
        df_dict = self._blocked_comparisons(return_df_as_value=False)
        df_dict = compute_comparison_vector_values(
            self.settings_obj, df_dict, self.execute_sql
        )

        if return_df_as_value:
            return df_dict["__splink__df_comparison_vectors"].df_value
        else:
            return df_dict

    def train_u_using_random_sampling(self, target_rows):

        estimate_u_values(self, self.input_dfs, target_rows)
        self.populate_m_u_from_trained_values()

    def train_m_from_label_column(self, label_colname):

        estimate_m_values_from_label_column(self, self.input_dfs, label_colname)
        self.populate_m_u_from_trained_values()

    def train_m_using_expectation_maximisation(
        self,
        blocking_rule,
        comparisons_to_deactivate=None,
        comparison_levels_to_reverse_blocking_rule=None,
        fix_proportion_of_matches=False,
        fix_u_probabilities=True,
        fix_m_probabilities=False,
    ):

        em_training_session = EMTrainingSession(
            self,
            blocking_rule,
            fix_u_probabilities=fix_u_probabilities,
            fix_m_probabilities=fix_m_probabilities,
            fix_proportion_of_matches=fix_proportion_of_matches,
            comparisons_to_deactivate=comparisons_to_deactivate,
            comparison_levels_to_reverse_blocking_rule=comparison_levels_to_reverse_blocking_rule,
        )

        em_training_session.train()

        self.populate_m_u_from_trained_values()

        self.populate_proportion_of_matches_from_trained_values()

        return em_training_session

    def populate_proportion_of_matches_from_trained_values(self):
        # Need access to here to the individual training session
        # their blocking rules and m and u values
        prop_matches_estimates = []
        for em_training_session in self.em_training_sessions:
            training_lambda = em_training_session.settings_obj._proportion_of_matches
            training_lambda_bf = prob_to_bayes_factor(training_lambda)
            reverse_levels = (
                em_training_session.comparison_levels_to_reverse_blocking_rule
            )

            global_prop_matches_fully_trained = True
            for reverse_level in reverse_levels:

                # Get comparison level on current settings obj
                cc = self.settings_obj._get_comparison_by_name(
                    reverse_level.comparison.comparison_name
                )

                cl = cc.get_comparison_level_by_comparison_vector_value(
                    reverse_level.comparison_vector_value
                )

                if cl.is_trained:
                    bf = cl.trained_m_median / cl.trained_u_median
                else:
                    bf = cl.bayes_factor
                    global_prop_matches_fully_trained = False

                training_lambda_bf = training_lambda_bf / bf
            p = bayes_factor_to_prob(training_lambda_bf)
            prop_matches_estimates.append(p)

        if not global_prop_matches_fully_trained:
            print(
                f"Proportion of matches not fully trained, current estimates are {prop_matches_estimates}"
            )
        else:
            print(
                f"Proportion of matches can now be estimated, estimates are {prop_matches_estimates}"
            )

        self.settings_obj._proportion_of_matches = median(prop_matches_estimates)

    def populate_m_u_from_trained_values(self):
        ccs = self.settings_obj.comparisons

        for cc in ccs:
            for cl in cc.comparison_levels:
                if cl.u_is_trained:
                    cl.u_probability = cl.trained_u_median
                if cl.m_is_trained:
                    cl.m_probability = cl.trained_m_median

    def train_m_and_u_using_expectation_maximisation(
        self,
        blocking_rule,
        fix_proportion_of_matches=False,
        comparisons_to_deactivate=None,
        fix_u_probabilities=False,
        fix_m_probabilities=False,
        comparison_levels_to_reverse_blocking_rule=None,
    ):
        return self.train_m_using_expectation_maximisation(
            blocking_rule,
            fix_proportion_of_matches=fix_proportion_of_matches,
            comparisons_to_deactivate=comparisons_to_deactivate,
            fix_u_probabilities=fix_u_probabilities,
            fix_m_probabilities=fix_m_probabilities,
            comparison_levels_to_reverse_blocking_rule=comparison_levels_to_reverse_blocking_rule,
        )

    def predict(self, return_df_as_value=True):
        df_dict = self.comparison_vectors(return_df_as_value=False)
        df_dict = predict(self.settings_obj, df_dict, self.execute_sql)

        if return_df_as_value:
            return df_dict["__splink__df_predict"].df_value
        else:
            return df_dict

    def execute_sql(self):
        pass
