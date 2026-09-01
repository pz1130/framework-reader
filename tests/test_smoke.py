import framework_reader


def test_package_exposes_calver_version():
    # CalVer: YYYY.MM，spec §6.2
    parts = framework_reader.__version__.split(".")
    assert len(parts) == 2
    assert len(parts[0]) == 4 and parts[0].isdigit()
    assert len(parts[1]) == 2 and parts[1].isdigit()
