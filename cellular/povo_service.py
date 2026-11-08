#!/usr/bin/env python3
"""JT-B1 povo data supervisor. No unlock codes, firmware flashing or OS resets."""
import os,sys,json,time,signal,fcntl,subprocess,re,ipaddress
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/jtb1-povo')
from gobinet_wds import Wds
from modem_session import Session
RUNTIME=Path('/run/jtb1-povo');running=True
BEGIN='# jtb1-povo begin\n';END='# jtb1-povo end\n'
def log(event,**data):print(json.dumps({'time':time.time(),'event':event,**data}),flush=True)
def dns_text(text,addresses):
 if text.count(BEGIN)!=text.count(END) or text.count(BEGIN)>1:raise ValueError('malformed DNS marker block')
 base=re.sub(r'(?m)^# jtb1-povo begin\n.*?^# jtb1-povo end\n?','',text,flags=re.S)
 if not addresses:return base
 addresses=[str(ipaddress.IPv4Address(x)) for x in addresses]
 return base+('' if not base or base.endswith('\n') else '\n')+BEGIN+''.join('nameserver '+x+'\n' for x in addresses)+END

def dns_update(addresses):
 path=Path('/etc/resolv.conf')
 if path.is_symlink():raise RuntimeError('refuse to replace symlink resolv.conf')
 original=path.read_text();updated=dns_text(original,addresses)
 if updated==original:return
 temp=path.with_name('resolv.conf.povo-new')
 try:
  temp.write_text(updated);os.chmod(temp,0o644)
  if path.read_text()!=original:return
  os.replace(temp,path)
 finally:
  try:temp.unlink()
  except FileNotFoundError:pass

def save_state(**state):
 temp=RUNTIME/'state.new';temp.write_text(json.dumps({'pid':os.getpid(),'updated_at':time.time(),**state})+'\n');os.chmod(temp,0o600);os.replace(temp,RUNTIME/'state.json')

def ip(*args,check=True):
 r=subprocess.run(['/sbin/ip',*args],capture_output=True,text=True)
 if check and r.returncode:raise RuntimeError('ip '+repr(args)+': '+r.stderr)
 return r

def delay(seconds):
 for _ in range(seconds):
  if not running:return
  time.sleep(1)

def prepare_modem():
 if Path('/proc/sys/kernel/osrelease').read_text().strip()!='3.0.8+':raise RuntimeError('kernel version does not match saved drivers')
 boot=Path('/sys/Howl_3g/boot_mode')
 if boot.exists() and boot.read_text().strip()!='0':raise RuntimeError('modem not in normal boot mode; do not alter it automatically')
 power=Path('/sys/Howl_3g/module_power')
 if power.exists() and power.read_text().strip()=='0':power.write_text('1\n')
 for name in ('sierra','GobiNet'):
  if not Path('/sys/module',name).exists():
   r=subprocess.run(['/sbin/insmod','/usr/local/lib/jtb1-povo/'+name+'.ko'],capture_output=True,text=True)
   if r.returncode:raise RuntimeError('insmod '+name+': '+r.stderr)
 for _ in range(30):
  if Path('/dev/qcqmi0').exists():break
  if not running:raise InterruptedError('stopped')
  time.sleep(1)
 else:raise TimeoutError('qcqmi0 not available')
 allowed=['AT+CPIN?','AT+CLCK="PN",2','AT+CGDCONT?','AT+CGDCONT=1,"IP","povo.jp"','AT+CFUN?','AT+CFUN=1','AT+CEREG?']
 with Session(allowed) as modem:
  if '+CPIN: READY' not in modem.command('AT+CPIN?'):raise RuntimeError('SIM not ready; no PIN or unlock guessing')
  if '+CLCK: 0' not in modem.command('AT+CLCK="PN",2'):raise RuntimeError('network lock not disabled; do not unlock again')
  contexts=modem.command('AT+CGDCONT?')
  if '+CGDCONT: 1,"IP","povo.jp"' not in contexts:
   response=modem.command('AT+CGDCONT=1,"IP","povo.jp"',30)
   if 'OK' not in response or 'ERROR' in response:raise RuntimeError('APN update pending; recheck after backoff')
  if '+CFUN: 1' not in modem.command('AT+CFUN?'):
   response=modem.command('AT+CFUN=1',30)
   if 'OK' not in response or 'ERROR' in response:raise RuntimeError('RF activation failed')
  for _ in range(12):
   response=modem.command('AT+CEREG?')
   if re.search(r'\+CEREG: \d+,[15](?:,|\r|$)',response):return
   if not running:raise InterruptedError('stopped')
   delay(5)
  raise RuntimeError('LTE not registered yet')

def main():
 global running
 if os.geteuid()!=0:raise PermissionError('root required')
 RUNTIME.mkdir(mode=0o755,parents=True,exist_ok=True)
 lock=os.open(RUNTIME/'lock',os.O_WRONLY|os.O_CREAT,0o600)
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:log('already_running');os.close(lock);return
 for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *a:globals().__setitem__('running',False))
 pid=RUNTIME/'pid';pid.write_text(str(os.getpid())+'\n');os.chmod(pid,0o600)
 try:
  while running:
   client=None;settings=None
   try:
    save_state(status='connecting');prepare_modem()
    if not running:break
    ip('link','set','usb0','up')
    client=Wds();client.start();settings=client.settings()
    ip('link','set','usb0','mtu',str(settings['mtu']))
    ip('addr','replace',settings['address']+'/'+str(settings['prefix']),'dev','usb0')
    ip('route','replace','default','via',settings['gateway'],'dev','usb0','metric','700')
    dns_update(settings['dns']);save_state(status='connected',**settings);log('connected',**settings)
    while running:
     if not client.connected():raise RuntimeError('WDS disconnected')
     dns_update(settings['dns']);save_state(status='connected',**settings)
     delay(15)
   except Exception as e:
    log('connection_error',error=str(e));save_state(status='reconnecting',error=str(e))
   finally:
    if client is not None:
     try:client.close()
     except Exception as e:log('close_error',error=str(e))
    if settings is not None:
     ip('route','del','default','dev','usb0','metric','700',check=False)
     ip('addr','del',settings['address']+'/'+str(settings['prefix']),'dev','usb0',check=False)
    try:dns_update([])
    except Exception as e:log('dns_cleanup_error',error=str(e))
   if running:delay(30)
 finally:
  save_state(status='stopped')
  try:pid.unlink()
  except FileNotFoundError:pass
  os.close(lock);log('stopped')
if __name__=='__main__':main()
