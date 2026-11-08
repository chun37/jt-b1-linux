"""AT session with parent-first serial-bridge pause and identity-checked restore."""
import os,signal,termios,select,time
from pathlib import Path
def owners():
 for p in Path('/proc').glob('[0-9]*'):
  try:
   if int(p.name)==os.getpid():continue
   links=[]
   for f in (p/'fd').iterdir():
    try:links.append(os.readlink(f))
    except OSError:pass
   if not any(x.startswith(('/dev/ttyUSB','/dev/ttyGS')) for x in links):continue
   if any(x.startswith('/dev/tty') and not x.startswith(('/dev/ttyUSB','/dev/ttyGS')) for x in links):raise RuntimeError('mixed tty owner')
   fields=(p/'stat').read_text().rsplit(')',1)[1].split()
   yield int(p.name),(p/'comm').read_text().strip(),fields[0],fields[19]
  except (FileNotFoundError,ProcessLookupError):continue
class Session:
 def __init__(self,allowed=(),allow_bridge_pause=None):
  self.allowed=set(allowed);self.held=[];self.fd=None
  self.allow_bridge_pause=(os.environ.get('JTB1_ALLOW_BRIDGE_PAUSE')=='1') if allow_bridge_pause is None else allow_bridge_pause
 def __enter__(self):
  try:
   if not self.allow_bridge_pause and list(owners()):
    raise RuntimeError('serial ports are busy; stop their owners manually (automatic bridge pause is opt-in)')
   # Freeze shell parents before enumerating children; otherwise a shell can
   # replace a cat after the process snapshot and steal the command response.
   for wanted in ('sh','cat'):
    added=[]
    for pid,comm,state,start in list(owners()):
     if comm!=wanted:continue
     if not self.allow_bridge_pause:raise RuntimeError('serial owner appeared during discovery; refusing to pause it')
     if state in ('T','t'):raise RuntimeError('already stopped owner')
     os.kill(pid,signal.SIGSTOP);self.held.append((pid,start));added.append(pid)
    deadline=time.monotonic()+2
    while added:
     pending=[pid for pid,_,state,_ in owners() if pid in added and state not in ('T','t')]
     if not pending:break
     if time.monotonic()>deadline:raise RuntimeError('bridge did not stop')
     time.sleep(.02)
   unexpected=[(pid,comm,state) for pid,comm,state,_ in owners() if state not in ('T','t') or pid not in {x[0] for x in self.held}]
   assert not unexpected,('unpaused serial owners',unexpected)
   ports=[]
   for p in Path('/sys/class/tty').glob('ttyUSB*'):
    q=(p/'device').resolve();i=next((x for x in (q,*q.parents) if (x/'bInterfaceNumber').is_file()),None)
    usb=next((x for x in (q,*q.parents) if (x/'idVendor').is_file()),None)
    if (i and usb and (i/'bInterfaceNumber').read_text().strip()=='03'
        and (usb/'idVendor').read_text().strip()=='1199'
        and (usb/'idProduct').read_text().strip()=='68a2'):ports.append('/dev/'+p.name)
   assert len(ports)==1
   self.fd=os.open(ports[0],os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
   a=termios.tcgetattr(self.fd);a[0]=a[1]=a[3]=0;a[2]=termios.CS8|termios.CREAD|termios.CLOCAL;a[4]=a[5]=termios.B115200;a[6][termios.VMIN]=a[6][termios.VTIME]=0;termios.tcsetattr(self.fd,termios.TCSANOW,a)
   return self
  except BaseException:self.__exit__(None,None,None);raise
 def command(self,cmd,timeout=10):
  assert cmd in self.allowed,'Unapproved AT command'
  termios.tcflush(self.fd,termios.TCIFLUSH);os.write(self.fd,cmd.encode()+b'\r');result=b'';end=time.monotonic()+timeout
  while time.monotonic()<end:
   if select.select([self.fd],[],[],.1)[0]:
    result+=os.read(self.fd,16384)
    if b'\r\nOK\r\n' in result or b'ERROR' in result:break
  return result.decode(errors='replace').strip()
 def __exit__(self,*args):
  if self.fd is not None:os.close(self.fd);self.fd=None
  for pid,start in reversed(self.held):
   try:
    if (Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]==start:os.kill(pid,signal.SIGCONT)
   except (FileNotFoundError,ProcessLookupError):pass
  self.held=[]
