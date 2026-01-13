def test_instance_config_returns_public_settings(api_client):
    response = api_client.request("GET", "/instance/config", expected=(200,))
    payload = response.json()

    assert "import_export_max_file_size_mb" in payload
    assert "disable_signup" in payload
    assert isinstance(payload["import_export_max_file_size_mb"], int)
    assert isinstance(payload["disable_signup"], bool)
