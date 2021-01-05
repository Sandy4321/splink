from copy import deepcopy

from .blocking import cartesian_block
from .gammas import add_gammas
from .maximisation_step import run_maximisation_step
from .model import Model
from .settings import complete_settings_dict

from pyspark.sql.dataframe import DataFrame
from pyspark.sql.session import SparkSession
from pyspark.sql.functions import lit


def _num_target_rows_to_rows_to_sample(target_rows):
    # Number of rows generated by cartesian product is
    # n(n-1)/2, where n is input rows
    # We want to set a target_rows = t, the number of
    # rows generated by Splink and find out how many input rows
    # we need to generate targer rows
    #     Solve t = n(n-1)/2 for n
    #     https://www.wolframalpha.com/input/?i=Solve%5Bt%3Dn+*+%28n+-+1%29+%2F+2%2C+n%5D
    sample_rows = 0.5 * ((8 * target_rows + 1) ** 0.5 + 1)
    return sample_rows


def estimate_u_values(
    settings: dict,
    spark: SparkSession,
    df: DataFrame = None,
    df_l: DataFrame = None,
    df_r: DataFrame = None,
    target_rows: int = 1e6,
):
    """Complete the `u_probabilities` section of the settings object
    by directly estimating `u_probabilities` from raw data (i.e. without
    the expectation maximisation algorithm).

    This procedure takes a sample of the data and generates the cartesian
    product of comparisons.  The validity of the u values rests on the
    assumption that the probability of two comparison in the cartesian
    product being a match is very low.  For large datasets, this is typically
    true.

    Args:
        settings (dict): splink settings dictionary
        spark (SparkSession): SparkSession object
        df_l (DataFrame, optional): A dataframe to link/dedupe. Where `link_type` is `link_only` or `link_and_dedupe`, one of the two dataframes to link. Should be ommitted `link_type` is `dedupe_only`.
        df_r (DataFrame, optional): A dataframe to link/dedupe. Where `link_type` is `link_only` or `link_and_dedupe`, one of the two dataframes to link. Should be ommitted `link_type` is `dedupe_only`.
        df (DataFrame, optional): The dataframe to dedupe. Where `link_type` is `dedupe_only`, the dataframe to dedupe. Should be ommitted `link_type` is `link_only` or `link_and_dedupe`.
        target_rows (int): The number of rows to generate in the cartesian product.
            If set too high, you can run out of memory.  Default value 1e6. Recommend settings to perhaps 1e7.

    Returns:
        dict: The input splink settings dictionary with the `u_probabilities` completed with
              the estimated values
    """

    # Preserve settings as provided
    orig_settings = deepcopy(settings)

    # Do not modify settings object provided by user either
    settings = deepcopy(settings)
    settings = complete_settings_dict(settings, spark)

    if settings["link_type"] == "dedupe_only":

        count_rows = df.count()
        sample_size = _num_target_rows_to_rows_to_sample(target_rows)

        proportion = sample_size / count_rows

        if proportion >= 1.0:
            proportion = 1.0

        df_s = df.sample(False, proportion)
        df_comparison = cartesian_block(settings, spark, df=df_s)

    if settings["link_type"] in ("link_only", "link_and_dedupe"):

        if settings["link_type"] == "link_only":
            count_rows = df_r.count() + df_l.count()
            sample_size = target_rows ** 0.5
            proportion = sample_size / count_rows

        if settings["link_type"] == "link_and_dedupe":
            count_rows = df_r.count() + df_l.count()
            sample_size = _num_target_rows_to_rows_to_sample(target_rows)
            proportion = sample_size / count_rows

        if proportion >= 1.0:
            proportion = 1.0

        df_r_s = df_r.sample(False, proportion)
        df_l_s = df_l.sample(False, proportion)
        df_comparison = cartesian_block(settings, spark, df_l=df_l_s, df_r=df_r_s)

    df_gammas = add_gammas(df_comparison, settings, spark)

    df_e_product = df_gammas.withColumn("match_probability", lit(0.0))

    model = Model(settings, spark)
    run_maximisation_step(df_e_product, model, spark)
    new_settings = model.current_settings_obj.settings_dict

    for i, col in enumerate(orig_settings["comparison_columns"]):
        u_probs = new_settings["comparison_columns"][i]["u_probabilities"]
        col["u_probabilities"] = u_probs

    return orig_settings
