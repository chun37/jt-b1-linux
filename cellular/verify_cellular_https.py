"""DNS and certificate-verified HTTPS bound to usb0, never the Wi-Fi route."""
import socket,ssl,struct,json,time,secrets,errno
from pathlib import Path
def stats():return {k:int(Path('/sys/class/net/usb0/statistics/'+k).read_text()) for k in ('rx_bytes','tx_bytes')}
def sock(typ):
 s=socket.socket(socket.AF_INET,typ);s.settimeout(30)
 s.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,dev.encode()+b'\0');s.bind((src,0))
 try:assert s.getsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,16).rstrip(b'\0')==b'usb0'
 except OSError as e:
  # Linux 3.0 accepts setsockopt, but getsockopt support arrived in 3.8.
  if e.errno!=errno.ENOPROTOOPT:raise
 return s

def skipname(data,offset):
 while True:
  assert offset<len(data)
  n=data[offset];offset+=1
  if n==0:return offset
  if n&0xc0==0xc0:assert offset<len(data);return offset+1
  assert n<=63 and offset+n<=len(data);offset+=n

def lookup(host):
 ident=secrets.randbits(16);qname=b''.join(bytes([len(x)])+x.encode() for x in host.split('.'))+b'\0'
 query=struct.pack('!6H',ident,0x100,1,0,0,0)+qname+struct.pack('!HH',1,1)
 with sock(socket.SOCK_DGRAM) as s:
  s.sendto(query,(dns,53));data,remote=s.recvfrom(4096)
  assert remote==(dns,53)
 header=struct.unpack_from('!6H',data);assert header[0]==ident and header[1]&0x8000 and header[1]&0xf==0
 offset=12
 for _ in range(header[2]):offset=skipname(data,offset)+4
 addresses=[]
 for _ in range(header[3]):
  offset=skipname(data,offset);typ,cls,ttl,size=struct.unpack_from('!HHIH',data,offset);offset+=10
  assert offset+size<=len(data)
  if typ==1 and cls==1 and size==4:addresses.append(socket.inet_ntoa(data[offset:offset+4]))
  offset+=size
 assert addresses
 print(json.dumps({'DNS_via_cellular':True,'host':host,'resolver':dns,'source':src,'addresses':addresses}),flush=True)
 return addresses
def main():
 import argparse,ipaddress
 global dev,src,dns
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--state',type=Path,default=Path('/run/jtb1-povo/state.json'))
 parser.add_argument('--confirm-network-test',action='store_true',help='send DNS and HTTPS over the cellular interface (data charges may apply)')
 args=parser.parse_args()
 if not args.confirm_network_test:parser.error('--confirm-network-test is required')
 if not __debug__:raise RuntimeError('do not run with python -O; validation uses assertions')
 state=json.loads(args.state.read_text())
 if state.get('status')!='connected' or state.get('interface')!='usb0':raise ValueError('connected usb0 state required')
 if not 0<=time.time()-state['updated_at']<45:raise ValueError('state file is stale')
 dev='usb0';src=str(ipaddress.IPv4Address(state['address']));dns=str(ipaddress.IPv4Address(state['dns'][0]))
 before=stats()
 context=ssl.create_default_context()
 assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
 for host in ['povo.jp','example.com']:
  addresses=lookup(host);errors=[];success=False
  for target in addresses:
   try:
    with sock(socket.SOCK_STREAM) as raw:
     raw.connect((target,443))
     with context.wrap_socket(raw,server_hostname=host) as tls:
      assert tls.getsockname()[0]==src
      tls.sendall(('HEAD / HTTP/1.1\r\nHost: '+host+'\r\nUser-Agent: JT-B1-cellular-verification\r\nConnection: close\r\n\r\n').encode())
      response=tls.recv(4096);line=response.split(b'\r\n',1)[0].decode('ascii');parts=line.split()
      assert parts[0].startswith('HTTP/') and 200<=int(parts[1])<400,line
      print(json.dumps({'HTTPS_via_cellular':True,'host':host,'peer':tls.getpeername(),'local':tls.getsockname(),'http_status':line,'TLS':tls.version(),'certificate_verified':True,'interface':dev}),flush=True)
      success=True;break
   except Exception as e:errors.append(str(e));print(json.dumps({'host':host,'target':target,'error':str(e)}),flush=True)
  assert success,errors
 before_end=stats();delta={k:before_end[k]-before[k] for k in before}
 assert all(v>0 for v in delta.values())
 print(json.dumps({'usb0_counter_delta':delta,'wifi_not_used_for_test':True}),flush=True)
 print('CELLULAR_DNS_AND_VERIFIED_HTTPS_SUCCESS=1',flush=True)

if __name__=='__main__':main()
