from app.data_transfer.dayone.models import DayOneWeather


def test_dayone_weather_wind_bearing_normalizes_invalid_values():
    assert DayOneWeather(windBearing=360).wind_bearing == 0
    assert DayOneWeather(windBearing=-1).wind_bearing is None
    assert DayOneWeather(windBearing=400).wind_bearing is None
    assert DayOneWeather(windBearing=359).wind_bearing == 359
