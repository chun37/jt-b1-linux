import unittest,sys,struct,importlib.util
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import gobinet_wds as w
import povo_service as s
import modem_session as m
# All addresses below are synthetic RFC 5737 documentation addresses.
class ProtocolTests(unittest.TestCase):
 def test_tlv_roundtrip(self):
  self.assertEqual(w.parse_tlvs(w.tlv(2,bytes(4))+w.tlv(20,b'povo.jp')),{2:bytes(4),20:b'povo.jp'})
 def test_tlv_rejects_truncation(self):
  for data in [b'\x01',b'\x01\x01',b'\x01\x04\x00abc']:
   with self.assertRaises(ValueError):w.parse_tlvs(data)
 def test_ipv4_byte_order(self):self.assertEqual(w.ipv4(bytes.fromhex('030200c0')),'192.0.2.3')
 def test_forbidden_wds_operation(self):
  obj=w.Wds.__new__(w.Wds)
  for msg in [0x40,0x41,0x42,0x0]:
   with self.assertRaises(ValueError):obj.call(msg)
 def fixture(self):
  return {30:bytes.fromhex('030200c0'),32:bytes.fromhex('010200c0'),33:bytes.fromhex('f8ffffff'),21:bytes.fromhex('356433c6'),22:bytes.fromhex('357100cb'),41:struct.pack('<I',1500),20:b'povo.jp'}
 def settings(self,values):
  obj=w.Wds.__new__(w.Wds);obj.call=lambda *a:values;return obj.settings()
 def test_documentation_network_fixture(self):
  result=self.settings(self.fixture());self.assertEqual(result['prefix'],29);self.assertEqual(result['gateway'],'192.0.2.1');self.assertEqual(result['dns'][0],'198.51.100.53')
 def test_reject_unexpected_apn(self):
  data=self.fixture();data[20]=b'other'
  with self.assertRaises(ValueError):self.settings(data)
 def test_reject_gateway_outside_network(self):
  data=self.fixture();data[32]=bytes.fromhex('01010101')
  with self.assertRaises(ValueError):self.settings(data)
 def test_reject_missing_dns(self):
  data=self.fixture();del data[21];del data[22]
  with self.assertRaises(ValueError):self.settings(data)
 def test_reject_bad_mtu(self):
  data=self.fixture();data[41]=struct.pack('<I',9999)
  with self.assertRaises(ValueError):self.settings(data)
class ReconnectTests(unittest.TestCase):
 def test_disconnected_status(self):
  obj=w.Wds.__new__(w.Wds);obj.call=lambda *a:{1:b'\x01'}
  self.assertFalse(obj.connected())
 def test_supervisor_recovers_and_cleans_both_sessions(self):
  import tempfile
  clients=[];closed=[];commands=[];delays=[]
  class Client:
   def __init__(self):self.index=len(clients);clients.append(self)
   def start(self):pass
   def settings(self):return {'interface':'usb0','address':'192.0.2.3','prefix':29,'gateway':'192.0.2.1','mtu':1500,'dns':['198.51.100.53'],'apn':'povo.jp'}
   def connected(self):return self.index>0
   def close(self):closed.append(self.index)
  def delay(n):
   delays.append(n)
   if n==15:s.running=False
  with tempfile.TemporaryDirectory() as d,patch.object(s,'RUNTIME',Path(d)),patch.object(s.os,'geteuid',lambda:0),patch.object(s.signal,'signal',lambda *a:None),patch.object(s,'prepare_modem',lambda:None),patch.object(s,'Wds',Client),patch.object(s,'ip',lambda *a,**kw:commands.append(a)),patch.object(s,'dns_update',lambda a:None),patch.object(s,'delay',delay),patch.object(s,'log',lambda *a,**kw:None):
   s.running=True
   try:s.main()
   finally:s.running=True
   self.assertEqual(len(clients),2);self.assertEqual(closed,[0,1]);self.assertIn(30,delays)
   self.assertEqual(len([c for c in commands if c[:3]==('route','del','default')]),2)
   self.assertEqual(__import__('json').loads((Path(d)/'state.json').read_text())['status'],'stopped')
class DnsTests(unittest.TestCase):
 base='nameserver 192.0.2.53\n# original\n'
 def test_preserve_wifi(self):self.assertTrue(s.dns_text(self.base,['198.51.100.53']).startswith(self.base))
 def test_idempotency(self):
  merged=s.dns_text(self.base,['198.51.100.53']);self.assertEqual(s.dns_text(merged,['198.51.100.53']),merged)
 def test_cleanup_roundtrip(self):self.assertEqual(s.dns_text(s.dns_text(self.base,['198.51.100.53']),[]),self.base)
 def test_new_dhcp_configuration_preserved(self):
  merged=s.dns_text('nameserver 192.0.2.54\n',['198.51.100.53']);self.assertTrue(merged.startswith('nameserver 192.0.2.54\n'))
 def test_malformed_block_rejected(self):
  with self.assertRaises(ValueError):s.dns_text(self.base+s.BEGIN,['198.51.100.53'])
 def test_address_injection_rejected(self):
  with self.assertRaises(ValueError):s.dns_text(self.base,['1.1.1.1\nnameserver evil'])
class BridgeTests(unittest.TestCase):
 def test_busy_ports_are_not_paused_without_opt_in(self):
  with patch.object(m,'owners',return_value=iter([(123,'cat','S','1000')])),patch.object(m.os,'kill') as kill:
   with self.assertRaises(RuntimeError):m.Session([],allow_bridge_pause=False).__enter__()
   kill.assert_not_called()
 def test_parent_before_replacement_cat_and_restore_after_failure(self):
  processes={100:['sh','S','1000'],101:['cat','S','1010']};events=[]
  def owners():return iter([(pid,*v) for pid,v in list(processes.items())])
  def kill(pid,sig):
   events.append((pid,sig));processes[pid][1]='T' if sig==m.signal.SIGSTOP else 'S'
   if pid==100 and sig==m.signal.SIGSTOP:
    processes.pop(101);processes[102]=['cat','S','1020']
  class FakePath:
   def __init__(self,path):self.path=path
   def __truediv__(self,part):return FakePath(self.path+'/'+str(part))
   def glob(self,*a):raise OSError('test port discovery failure')
   def read_text(self):
    pid=int(self.path.split('/')[2]);return str(pid)+' (cat) '+processes[pid][1]+' '+' '.join(['0']*18+[processes[pid][2]])
  session=m.Session([],allow_bridge_pause=True)
  with patch.object(m,'owners',owners),patch.object(m,'Path',FakePath),patch.object(m.os,'kill',kill):
   with self.assertRaises(OSError):session.__enter__()
  self.assertEqual(events,[(100,m.signal.SIGSTOP),(102,m.signal.SIGSTOP),(102,m.signal.SIGCONT),(100,m.signal.SIGCONT)])
  self.assertEqual(session.held,[])
if __name__=='__main__':unittest.main(verbosity=2)
