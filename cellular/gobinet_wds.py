"""Minimal GobiNet WDS SDU adapter for the verified MC7700, no DMS/PIN operations."""
import os,fcntl,struct,select,time,ipaddress

def tlv(kind,data):return struct.pack('<BH',kind,len(data))+data

def parse_tlvs(data):
 values={};offset=0
 while offset<len(data):
  if offset+3>len(data):raise ValueError('truncated TLV header')
  kind,size=struct.unpack_from('<BH',data,offset);offset+=3
  if offset+size>len(data):raise ValueError('truncated TLV value')
  values[kind]=data[offset:offset+size];offset+=size
 return values

def ipv4(data):
 if len(data)!=4:raise ValueError('invalid IPv4 TLV')
 return str(ipaddress.IPv4Address(int.from_bytes(data,'little')))

class Wds:
 def __init__(self,path='/dev/qcqmi0'):
  self.fd=os.open(path,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK);self.transaction=0;self.handle=None
  try:fcntl.ioctl(self.fd,0x8be1,1)
  except BaseException:os.close(self.fd);raise
 def call(self,message,payload=b'',timeout=30):
  if message not in (0x20,0x21,0x22,0x2d):raise ValueError('unapproved WDS message')
  self.transaction=self.transaction%65535+1
  request=struct.pack('<BHHH',0,self.transaction,message,len(payload))+payload
  if os.write(self.fd,request)!=len(request):raise OSError('short QMI write')
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   if not select.select([self.fd],[],[],.25)[0]:continue
   data=os.read(self.fd,16384)
   if len(data)<7 or struct.unpack_from('<HH',data,1)!=(self.transaction,message):continue
   if len(data)!=7+struct.unpack_from('<H',data,5)[0]:raise ValueError('QMI SDU length mismatch')
   values=parse_tlvs(data[7:])
   if values.get(2)!=bytes(4):raise RuntimeError('WDS '+hex(message)+' failure '+str({str(k):v.hex() for k,v in values.items()}))
   return values
  raise TimeoutError('WDS '+hex(message))
 def start(self):
  values=self.call(0x20,tlv(0x14,b'povo.jp')+tlv(0x16,b'\0')+tlv(0x19,b'\x04'),90)
  if len(values.get(1,b''))!=4:raise ValueError('missing network handle')
  self.handle=values[1]
 def settings(self):
  values=self.call(0x2d,tlv(0x10,struct.pack('<I',0x2318)))
  addr=ipv4(values[0x1e]);gw=ipv4(values[0x20]);mask=ipv4(values[0x21]);interface=ipaddress.IPv4Interface(addr+'/'+mask)
  if interface.ip.is_unspecified or ipaddress.IPv4Address(gw) not in interface.network:raise ValueError('invalid network settings')
  mtu=struct.unpack('<I',values.get(0x29,struct.pack('<I',1500)))[0]
  if not 576<=mtu<=1500:raise ValueError('invalid MTU')
  dns=[ipv4(values[k]) for k in (0x15,0x16) if k in values and values[k]!=bytes(4)]
  if not dns:raise ValueError('missing DNS')
  if values.get(0x14,b'povo.jp')!=b'povo.jp':raise ValueError('unexpected APN')
  return {'interface':'usb0','address':addr,'prefix':interface.network.prefixlen,'gateway':gw,'mtu':mtu,'dns':dns,'apn':'povo.jp'}
 def connected(self):return self.call(0x22).get(1,b'')[:1]==b'\x02'
 def close(self):
  try:
   if self.handle is not None:self.call(0x21,tlv(1,self.handle),15)
  finally:os.close(self.fd);self.fd=None;self.handle=None
