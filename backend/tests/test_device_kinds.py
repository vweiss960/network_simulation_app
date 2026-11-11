from app.services.device_kinds import KINDS, get_kind


def test_xrd_interface_names():
    ifs = get_kind("cisco_xrd").interfaces(2)
    assert ifs == [
        {"name": "GigabitEthernet0/0/0/0", "kernel": "Gi0-0-0-0"},
        {"name": "GigabitEthernet0/0/0/1", "kernel": "Gi0-0-0-1"},
    ]


def test_iosxe_vm_skips_mgmt_gi1():
    ifs = get_kind("cisco_c8000v").interfaces(2)
    assert ifs[0] == {"name": "GigabitEthernet2", "kernel": "eth1"}
    assert ifs[1] == {"name": "GigabitEthernet3", "kernel": "eth2"}


def test_vios_groups_of_four():
    names = [i["name"] for i in get_kind("cisco_vios").interfaces(5)]
    assert names == ["GigabitEthernet0/1", "GigabitEthernet0/2", "GigabitEthernet0/3",
                     "GigabitEthernet1/0", "GigabitEthernet1/1"]


def test_interface_count_is_clamped():
    k = get_kind("cisco_c8000v")
    assert len(k.interfaces(100)) == k.max_interfaces


def test_every_kind_has_unique_kernel_names():
    for k in KINDS.values():
        kern = [i["kernel"] for i in k.interfaces(k.max_interfaces)]
        assert len(set(kern)) == len(kern), k.kind


def test_unknown_kind_falls_back_to_linux():
    assert get_kind("nope").kind == "linux"
