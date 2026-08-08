from perla_extract.pydantic_model_reduced import PerovskiteSolarCell


def test_performance_aggregation_distinguishes_champion_from_average():
    champion = PerovskiteSolarCell(
        pce={"value": 22.04, "unit": "%"},
        performance_aggregation="champion",
        averaged_quantities=False,
    )
    averaged = PerovskiteSolarCell(
        pce={"value": 21.5, "unit": "%"},
        performance_aggregation="mean",
        averaged_quantities=True,
        number_devices=20,
    )

    assert champion.performance_aggregation == "champion"
    assert averaged.performance_aggregation == "mean"
    assert averaged.number_devices == 20
