"""GSDI delayed read-only status, then key read only if status is recognized.
Never sends legacy direct 0x44 (writes EFS), deactivate, unblock, or reset.
"""
import os,signal,struct,time,select,termios,importlib.util,json,subprocess,hashlib
from pathlib import Path
import diag_frames as m
def packet(sub):
 assert sub in (0x42,0x44)
 p=struct.pack('<BBHII BB',0x80,33,0x48,0,sub,1,0)+bytes(16)
 if sub==0x44:p+=struct.pack('<I',0) # NW key, NOT unblock key
 assert len(p)==(30 if sub==0x42 else 34)
 return p
assert packet(0x42).hex()=='802148000000000042000000010000000000000000000000000000000000'
import sys
if __name__ != '__main__':
 raise RuntimeError('hardware script must not be imported; use diag_frames for offline tests')
if not __debug__:
 raise SystemExit('Do not use python -O: protocol guards require assertions')
if sys.argv[1:] != ['--execute-on-owned-device']:
 raise SystemExit('No device access. Read README.md; requires --execute-on-owned-device')
if os.geteuid() != 0:
 raise SystemExit('Requires root on the Linux host that owns the modem serial ports')

held=[];fd=None
try:
 for p in Path('/proc').glob('[0-9]*'):
  try:
   if int(p.name)==os.getpid() or (p/'comm').read_text().strip() not in ('cat','sh'):continue
   links=[]
   for f in (p/'fd').iterdir():
    try:links.append(os.readlink(f))
    except OSError:pass
   if not any(x.startswith(('/dev/ttyUSB','/dev/ttyGS')) for x in links):continue
   if any(x.startswith('/dev/tty') and not x.startswith(('/dev/ttyUSB','/dev/ttyGS')) for x in links):raise RuntimeError('mixed tty owner')
   if '\nState:\tT' in (p/'status').read_text():raise RuntimeError('already stopped')
   start=(p/'stat').read_text().rsplit(')',1)[1].split()[19]
   os.kill(int(p.name),signal.SIGSTOP);held.append((int(p.name),start))
  except (FileNotFoundError,ProcessLookupError):continue
 print('PAUSED_FORWARDER_COUNT='+str(len(held)),flush=True)
 ports=[]
 for p in Path('/sys/class/tty').glob('ttyUSB*'):
  q=(p/'device').resolve();i=next((x for x in (q,*q.parents) if (x/'bInterfaceNumber').is_file()),None)
  if i and (i/'bInterfaceNumber').read_text().strip()=='00':ports.append('/dev/'+p.name)
 assert len(ports)==1
 fd=os.open(ports[0],os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
 a=termios.tcgetattr(fd);a[0]=a[1]=a[3]=0;a[2]=termios.CS8|termios.CREAD|termios.CLOCAL;a[4]=a[5]=termios.B115200;a[6][termios.VMIN]=a[6][termios.VTIME]=0;termios.tcsetattr(fd,termios.TCSANOW,a)
 def read_request(sub):
  p=packet(sub);termios.tcflush(fd,termios.TCIFLUSH);os.write(fd,m.frame(p));buf=b'';end=time.monotonic()+6
  while time.monotonic()<end:
   if not select.select([fd],[],[],.15)[0]:continue
   buf+=os.read(fd,16384)
   if len(buf)>65536:raise RuntimeError('excess diagnostic input')
   while b'\x7e' in buf:
    raw,buf=buf.split(b'\x7e',1)
    if not raw:continue
    r=m.unframe(raw)
    if not r:continue
    if r[0] in (0x13,0x14,0x15,0x18,0x47):
     print(json.dumps({'subcommand':hex(sub),'diag_rejection':hex(r[0]),'response_length':len(r)}),flush=True);return None
    if r[:4]==p[:4] and len(r)>=24:
     status=struct.unpack_from('<I',r,4)[0];rid,count=struct.unpack_from('<HH',r,8)
     response_sub,gsdi_status=struct.unpack_from('<II',r,16)
     print(json.dumps({'subcommand':hex(sub),'response_subcommand':hex(response_sub),'diag_status':status,'gsdi_status':gsdi_status,'count':count,'response_length':len(r)}),flush=True)
     if status!=0 or gsdi_status!=0:return None
     if response_sub==sub and count>0:return r
  print(json.dumps({'subcommand':hex(sub),'no_successful_delayed_reply':True}),flush=True);return None
 status=read_request(0x42)
 if status is not None:
  print('GSDI_READONLY_STATUS_RECOGNIZED=1',flush=True)
  result=read_request(0x44)
  if result is not None and len(result)>=57:
   imeilen=struct.unpack_from('<i',result,24)[0];keylen=struct.unpack_from('<i',result,44)[0]
   imei=result[28:44].split(b'\0',1)[0];key=result[48:57].split(b'\0',1)[0]
   valid=imeilen==len(imei)+1 and len(imei)==15 and imei.isdigit() and keylen==len(key)+1 and len(key)==8 and key.isdigit()
   print(json.dumps({'key_read_format_valid':valid,'imei_length':len(imei),'key_length':len(key)}),flush=True)
   if valid:
    out=os.open('/tmp/gsdi-nw-key.private',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(out,'wb') as f:f.write(imei+b'\n'+key+b'\n')
    print('PRIVATE_NETWORK_KEY_SAVED=1',flush=True)
 os.close(fd);fd=None

finally:
 if fd is not None:os.close(fd)
 for pid,start in reversed(held):
  try:
   if (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]==start:os.kill(pid,signal.SIGCONT);print('RESUMED_FORWARDER='+str(pid),flush=True)
  except (FileNotFoundError,ProcessLookupError):pass
print('GSDI_READONLY_PROBE_DONE=1',flush=True)
