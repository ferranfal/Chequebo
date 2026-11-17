import csv
from types import SimpleNamespace as NS

import pytest

from chequebo import metrics
from chequebo.session import SessionWriter, export_samples, export_maxima, load_session
from test_system import FakeGPU


def test_network_rates_reset_and_adapter_changes(monkeypatch):
    monkeypatch.setattr(metrics,'GPU',FakeGPU)
    network={'eth0':NS(bytes_recv=100_000_000,bytes_sent=200_000_000)}
    monkeypatch.setattr(metrics.SystemSampler,'_network',staticmethod(lambda:network))
    sampler=metrics.SystemSampler()
    assert sampler.sample()['net_recv_mbps'] is None
    monkeypatch.setattr(metrics.time,'monotonic',lambda:sampler.previous+2)
    network={'eth0':NS(bytes_recv=104_000_000,bytes_sent=201_000_000)}
    sample=sampler.sample()
    assert sample['net_recv_mbps'] == pytest.approx(16)
    assert sample['net_sent_mbps'] == pytest.approx(4)
    network={'eth0':NS(bytes_recv=0,bytes_sent=0)}
    assert sampler.sample()['net_recv_mbps'] == 0
    network={'wifi0':NS(bytes_recv=999_000_000,bytes_sent=999_000_000)}
    assert sampler.sample()['net_recv_mbps'] is None
    network={'wifi0':NS(bytes_recv=1_000_000_000,bytes_sent=999_000_000)}
    assert sampler.sample()['net_recv_mbps'] == pytest.approx(4)
    network=None
    assert sampler.sample()['net_sent_mbps'] is None
    sampler.close()


def test_network_uses_physical_adapters_without_loopback_or_vpn_duplicates(monkeypatch):
    counters={name:NS(bytes_recv=100,bytes_sent=200) for name in ['lo','eth0','tun0','docker0']}
    monkeypatch.setattr(metrics.psutil,'net_io_counters',lambda **kwargs:counters)
    monkeypatch.setattr(metrics.Path,'exists',lambda path:str(path)=='/sys/class/net/eth0/device')
    assert list(metrics.SystemSampler._network()) == ['eth0']


def test_network_recording_exports_and_peaks(tmp_path):
    path=tmp_path/'net.chequebo'
    writer=SessionWriter(path,{'scope':'system'})
    writer.append({'elapsed':0,'net_recv_mbps':None,'net_sent_mbps':None})
    writer.append({'elapsed':1,'net_recv_mbps':16,'net_sent_mbps':4})
    writer.append({'elapsed':2,'net_recv_mbps':3,'net_sent_mbps':9})
    writer.close()
    metadata,samples=load_session(path)
    export_samples(tmp_path/'samples.csv',samples,metadata)
    export_maxima(tmp_path/'peaks.csv',samples,0,2,metadata)
    with (tmp_path/'samples.csv').open() as file: rows=list(csv.DictReader(file))
    assert rows[0]['net_recv_mbps'] == '' and rows[1]['net_recv_mbps'] == '16'
    with (tmp_path/'peaks.csv').open() as file: peaks={r['resource']:r for r in csv.DictReader(file)}
    assert peaks['Network download']['recording_peak'] == '16'
    assert peaks['Network upload']['recording_peak'] == '9'
    assert peaks['Network download']['unit'] == 'Mb/s'
