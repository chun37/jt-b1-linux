#define _GNU_SOURCE
#include <linux/input.h>
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>

static volatile sig_atomic_t stopping;
static void stop(int s) { (void)s; stopping=1; }
static int scale(int v,int lo,int hi,int n) {
    if(hi<=lo || n<1) return -1;
    if(v<lo) v=lo;
    if(v>hi) v=hi;
    return (int)(((int64_t)v-lo)*(n-1)/(hi-lo));
}
static long long now_ms(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return (long long)t.tv_sec*1000+t.tv_nsec/1000000;
}
struct pixel { size_t pos; uint32_t old; };
static struct pixel saved[100];
static int nsaved;
static const uint32_t ink=0xff32e6ff;
static void erase(volatile uint32_t *fb) {
    for(int i=0;i<nsaved;i++) if(fb[saved[i].pos]==ink) fb[saved[i].pos]=saved[i].old;
    nsaved=0;
}
static void dot(volatile uint32_t *fb,int x,int y,int w,int h,int stride) {
    erase(fb);
    for(int d=-16;d<=16;d++) {
        int xx[2]={x+d,x}, yy[2]={y,y+d};
        for(int k=0;k<2;k++) {
            if(k==1 && d==0) continue;
            if(xx[k]<0 || yy[k]<0 || xx[k]>=w || yy[k]>=h) continue;
            size_t p=(size_t)yy[k]*stride+xx[k];
            saved[nsaved++]=(struct pixel){p,fb[p]}; fb[p]=ink;
        }
    }
}
static int find_input(char *path,struct input_absinfo *ax,struct input_absinfo *ay) {
    for(int i=0;i<64;i++) {
        snprintf(path,64,"/dev/input/event%d",i);
        int f=open(path,O_RDONLY|O_NONBLOCK|O_CLOEXEC); if(f<0) continue;
        char name[128]={0};
        if(ioctl(f,EVIOCGNAME(sizeof(name)),name)>=0 && !strcmp(name,"Atmel maXTouch Touchscreen") &&
           ioctl(f,EVIOCGABS(ABS_MT_POSITION_X),ax)==0 && ioctl(f,EVIOCGABS(ABS_MT_POSITION_Y),ay)==0 &&
           ax->maximum>ax->minimum && ay->maximum>ay->minimum) return f;
        close(f);
    }
    return -1;
}
int main(int argc,char **argv) {
    if(argc==2 && !strcmp(argv[1],"--selftest")) {
        if(scale(0,0,1023,1024)!=0 || scale(1023,0,1023,1024)!=1023 ||
           scale(599,0,599,600)!=599 || scale(-1,0,1023,1024)!=0 ||
           scale(2000,0,1023,1024)!=1023 || scale(5,5,5,600)!=-1 ||
           scale(15,10,20,101)!=50) return 1;
        puts("7 coordinate tests passed"); return 0;
    }
    int probe=argc==2 && !strcmp(argv[1],"--probe");
    char path[64]; struct input_absinfo ax,ay;
    int input=find_input(path,&ax,&ay);
    if(input<0) { fprintf(stderr,"touch device unavailable\n"); return 1; }
    printf("device=%s x=%d..%d y=%d..%d\n",path,ax.minimum,ax.maximum,ay.minimum,ay.maximum); fflush(stdout);
    if(probe) { close(input); return 0; }
    int lock=open("/run/jtb1-touch.lock",O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)<0) return 1;
    int f=open("/dev/fb0",O_RDWR|O_CLOEXEC);
    struct fb_var_screeninfo v; struct fb_fix_screeninfo fix;
    if(f<0 || ioctl(f,FBIOGET_VSCREENINFO,&v)<0 || ioctl(f,FBIOGET_FSCREENINFO,&fix)<0 ||
       v.xres!=1024 || v.yres!=600 || v.bits_per_pixel!=32 || v.xoffset || v.yoffset ||
       v.red.offset!=16 || v.green.offset!=8 || v.blue.offset!=0 ||
       fix.line_length!=4096 || fix.smem_len<4096*600) return 1;
    volatile uint32_t *fb=mmap(NULL,fix.smem_len,PROT_READ|PROT_WRITE,MAP_SHARED,f,0);
    if(fb==MAP_FAILED) return 1;
    signal(SIGTERM,stop); signal(SIGINT,stop); signal(SIGHUP,stop);
    int slot=0,down=0,x=0,y=0,have_x=0,have_y=0,lastdown=0,dropped=0;
    unsigned long frames=0,presses=0; long long expiry=0,laststatus=0;
    while(!stopping) {
        struct pollfd p={input,POLLIN,0};
        int r=poll(&p,1,100); long long t=now_ms();
        if(r<0) { if(errno==EINTR) continue; break; }
        if(p.revents&(POLLERR|POLLHUP|POLLNVAL)) break;
        if(expiry && t>=expiry) { erase(fb); expiry=0; }
        struct input_event e;
        while(r>0 && read(input,&e,sizeof(e))==(ssize_t)sizeof(e)) {
            if(e.type==EV_SYN && e.code==SYN_DROPPED) {
                dropped=1; down=0; have_x=have_y=0; erase(fb); continue;
            }
            if(dropped) {
                if(e.type==EV_SYN && e.code==SYN_REPORT) { dropped=0; lastdown=0; slot=0; }
                continue;
            }
            if(e.type==EV_ABS) {
                if(e.code==ABS_MT_SLOT) slot=e.value;
                else if(slot==0) {
                    if(e.code==ABS_MT_TRACKING_ID) down=e.value>=0;
                    else if(e.code==ABS_MT_POSITION_X) { x=e.value; have_x=1; }
                    else if(e.code==ABS_MT_POSITION_Y) { y=e.value; have_y=1; }
                }
            }
            if(e.type==EV_SYN && e.code==SYN_REPORT) {
                frames++;
                int sx=scale(x,ax.minimum,ax.maximum,1024),sy=scale(y,ay.minimum,ay.maximum,600);
                if(down && have_x && have_y) { dot(fb,sx,sy,1024,600,1024); expiry=t+1200; }
                if(down && !lastdown) presses++;
                if(down!=lastdown || t-laststatus>=100) {
                    FILE *s=fopen("/run/jtb1-touch.status.tmp","w");
                    if(s) { fprintf(s,"device=%s\ndown=%d\nx=%d\ny=%d\nframes=%lu\npresses=%lu\n",path,down,sx,sy,frames,presses); fclose(s); rename("/run/jtb1-touch.status.tmp","/run/jtb1-touch.status"); }
                    laststatus=t;
                }
                if(down!=lastdown) { printf("%s x=%d y=%d presses=%lu\n",down?"DOWN":"UP",sx,sy,presses); fflush(stdout); }
                lastdown=down;
            }
        }
    }
    erase(fb); munmap((void *)fb,fix.smem_len); close(f); close(input); close(lock);
    return stopping?0:1;
}
