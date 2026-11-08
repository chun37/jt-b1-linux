"""Use only the GSDI-read NW code for the same IMEI, one guarded attempt."""
import os,stat,signal,termios,select,time,re,json,subprocess
from pathlib import Path
import sys
if __name__ != '__main__':
 raise RuntimeError('hardware script must not be imported; use diag_frames for offline tests')
if not __debug__:
 raise SystemExit('Do not use python -O: protocol guards require assertions')
if sys.argv[1:] != ['--execute-on-owned-device']:
 raise SystemExit('No device access. Read README.md; requires --execute-on-owned-device')
if os.geteuid() != 0:
 raise SystemExit('Requires root on the Linux host that owns the modem serial ports')

private=Path('/tmp/gsdi-nw-key.private');s=private.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o600
imei,key=private.read_bytes().splitlines()
assert len(imei)==15 and imei.isdigit() and len(key)==8 and key.isdigit()
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
   if '\nState:\tT' in (p/'status').read_text():raise RuntimeError('already stopped owner')
   start=(p/'stat').read_text().rsplit(')',1)[1].split()[19]
   os.kill(int(p.name),signal.SIGSTOP);held.append((int(p.name),start))
  except (FileNotFoundError,ProcessLookupError):continue
 print('PAUSED_FORWARDER_COUNT='+str(len(held)),flush=True)
 ports=[]
 for p in Path('/sys/class/tty').glob('ttyUSB*'):
  q=(p/'device').resolve();i=next((x for x in (q,*q.parents) if (x/'bInterfaceNumber').is_file()),None)
  if i and (i/'bInterfaceNumber').read_text().strip()=='03':ports.append('/dev/'+p.name)
 assert len(ports)==1
 fd=os.open(ports[0],os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
 a=termios.tcgetattr(fd);a[0]=a[1]=a[3]=0;a[2]=termios.CS8|termios.CREAD|termios.CLOCAL;a[4]=a[5]=termios.B115200;a[6][termios.VMIN]=a[6][termios.VTIME]=0;termios.tcsetattr(fd,termios.TCSANOW,a)
 unlock=b'AT+CLCK="PN",0,"'+key+b'"'
 def send(cmd,secret=False):
  assert cmd in [b'AT+CGMM',b'AT+CGMR',b'AT+CGSN',b'AT+CPIN?',b'AT+CLCK="PN",2',unlock]
  termios.tcflush(fd,termios.TCIFLUSH);os.write(fd,cmd+b'\r');result=b'';end=time.monotonic()+(30 if cmd==unlock else 6)
  while time.monotonic()<end:
   if select.select([fd],[],[],.1)[0]:
    result+=os.read(fd,16384)
    if b'\r\nOK\r\n' in result or b'ERROR' in result:break
  ok=b'\r\nOK\r\n' in result and b'ERROR' not in result
  info={'operation':'AT+CLCK PN disable with verified code' if cmd==unlock else cmd.decode(),'ok':ok,'error':b'ERROR' in result}
  if not secret:info['response']=result.decode(errors='replace').strip()
  print(json.dumps(info),flush=True)
  return result,ok
 r,ok=send(b'AT+CGMM');assert ok and b'MC7700' in r
 r,ok=send(b'AT+CGMR');assert ok and b'SWI9200X_03.05.19.00Aap' in r
 r,ok=send(b'AT+CGSN',True);matches=re.findall(rb'(?m)^([0-9]{15})\r?$',r)
 assert ok and matches==[imei],'IMEI mismatch or invalid reply'
 print('PRIVATE_CODE_IMEI_MATCH=1',flush=True)
 r,ok=send(b'AT+CLCK="PN",2');assert ok and b'+CLCK: 1' in r,'PN is not locked; do not attempt'
 r,ok=send(b'AT+CPIN?');assert ok and b'PH-NET PIN' in r
 marker=os.open('/tmp/gsdi-nck-used.once',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(marker,'wb') as f:f.write(b'ONE_VERIFIED_NW_CODE_ATTEMPT\n');f.flush();os.fsync(f.fileno())
 r,unlock_ok=send(unlock,True)
 print('VERIFIED_CODE_ATTEMPTS=1',flush=True)
 r,pn_ok=send(b'AT+CLCK="PN",2');pn_clear=pn_ok and b'+CLCK: 0' in r
 r,pin_ok=send(b'AT+CPIN?');pin_ready=pin_ok and b'+CPIN: READY' in r
 os.close(fd);fd=None
 print(json.dumps({'unlock_command_ok':unlock_ok,'PN_unlocked':pn_clear,'SIM_ready':pin_ready}),flush=True)
 assert unlock_ok and pn_clear and pin_ready,'No retry; inspect the logged state'
 print('VERIFIED_NETWORK_UNLOCK_SUCCESS=1',flush=True)
finally:
 if fd is not None:os.close(fd)
 for pid,start in reversed(held):
  try:
   if (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]==start:os.kill(pid,signal.SIGCONT);print('RESUMED_FORWARDER='+str(pid),flush=True)
  except (FileNotFoundError,ProcessLookupError):pass
