#define main touch_main
#include "touch.c"
#undef main
#include <assert.h>
int main(void) {
    assert(touch_main(2,(char *[]){"touch","--selftest"})==0);
    uint32_t *mem=calloc(1024*600+2,sizeof(uint32_t));
    assert(mem); mem[0]=123; mem[1024*600+1]=456;
    uint32_t *screen=mem+1;
    int points[][2]={{0,0},{1023,599},{0,599},{1023,0},{512,300}};
    for(int i=0;i<5;i++) {
        dot(screen,points[i][0],points[i][1],1024,600,1024);
        assert(nsaved>0 && nsaved<100);
        assert(screen[points[i][1]*1024+points[i][0]]==ink);
        erase(screen);
        for(int j=0;j<1024*600;j++) assert(screen[j]==0);
        assert(mem[0]==123 && mem[1024*600+1]==456);
    }
    dot(screen,500,300,1024,600,1024);
    screen[300*1024+500]=0x11223344;
    erase(screen);
    assert(screen[300*1024+500]==0x11223344);
    dot(screen,500,300,1024,600,1024);
    dot(screen,600,400,1024,600,1024);
    assert(screen[300*1024+500]==0x11223344);
    erase(screen);
    free(mem);
    puts("7 marker tests passed (5 edges/center, external repaint, movement)");
}
