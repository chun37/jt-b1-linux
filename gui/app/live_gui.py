"""Standalone touch Spotify GUI: Web API metadata + official local Soloist audio."""
import argparse
import io
import json
import os
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request
from PIL import Image, ImageDraw
import display_preview as surface
from japanese_input import Keyboard, KANA, ASCII, SMALL, VOICED, katakana
from spotify_backend import WebAPI, Player, BackendError, STATE, opener, ctl, URI, start_audio
from auth_helper import atomic_json


def cover_url(covers):
    sizes={'small':0,'default':1,'large':2,'xlarge':3}
    valid=[cover for cover in covers if isinstance(cover,dict) and cover.get('url')]
    return max(valid,key=lambda cover:sizes.get(cover.get('size'),-1))['url'] if valid else None


class Workers:
    def __init__(self, api=None, player=None):
        self.api=api or WebAPI()
        self.player=player or Player(starter=start_audio)
        self.api_jobs=queue.Queue(maxsize=12)
        self.controls=queue.Queue(maxsize=12)
        self.results=queue.SimpleQueue()
        self.stop=threading.Event()
        self.generation=0
        self.threads=[threading.Thread(target=self.api_loop,daemon=True),threading.Thread(target=self.control_loop,daemon=True)]
        for thread in self.threads:thread.start()

    def submit(self,job):
        try:self.api_jobs.put_nowait(job)
        except queue.Full:raise BackendError('取得中です。少し待って再度操作してください')

    def api_loop(self):
        refresh_at=0
        while not self.stop.is_set():
            try:job=self.api_jobs.get(timeout=0.2)
            except queue.Empty:job=None
            if self.stop.is_set():break
            if job:
                kind,generation,params=job
                if kind=='page' and generation!=self.generation:continue
                try:
                    if kind=='page':result=self.api.page(**params)
                    else:result=self.image(params)
                    self.results.put((kind,generation,result,None))
                except Exception as error:
                    message=str(error) if isinstance(error,BackendError) else '通信を確認して再試行してください'
                    self.results.put((kind,generation,None,message))
            if time.monotonic()>=refresh_at and not self.stop.is_set():
                try:
                    if self.api.cooldown<=time.time():self.api.token()
                    self.results.put(('auth',0,True,None));refresh_at=time.monotonic()+30
                except Exception:
                    self.results.put(('auth',0,False,None));refresh_at=time.monotonic()+60

    def image(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in ('i.scdn.co','mosaic.scdn.co','seeded-session-images.scdn.co','image-cdn-ak.spotifycdn.com') or parsed.username or parsed.port not in (None,443):
            raise BackendError('未対応の画像URLです')
        with opener().open(Request(url),timeout=6) as response:
            content=response.read(1024*1024+1)
        if len(content)>1024*1024:raise BackendError('画像が大きすぎます')
        with Image.open(io.BytesIO(content)) as original:
            if original.width*original.height>4_000_000:raise BackendError('画像サイズが上限を超えています')
            original.thumbnail((280,280))
            return original.convert('RGBA').copy()

    def control_loop(self):
        while not self.stop.is_set():
            try:command,value=self.controls.get(timeout=0.2)
            except queue.Empty:continue
            if self.stop.is_set():break
            try:
                self.player.action(command,value)
                self.results.put(('control',0,ctl('now'),None))
            except Exception as error:
                message=str(error) if isinstance(error,BackendError) else '再生状態を確認してください'
                self.results.put(('control',0,None,message))

    def close(self):self.stop.set()
    def join(self):
        for thread in self.threads:thread.join(timeout=35)


class LiveView(surface.View):
    def __init__(self,font,catalog=None,workers=None,config=None,dictionary=None):
        super().__init__(font,{})
        self.workers=workers or Workers()
        self.config=Path(config or STATE/'gui.json')
        try:saved=json.loads(self.config.read_text())
        except (OSError,ValueError):saved={}
        self.orientation='ccw'  # System shell fixes portrait orientation.
        self.keyboard=Keyboard(dictionary or surface.BASE/'share/kana.sqlite')
        self.keyboard.set(saved.get('query',''))
        self.search_type='track'
        self.search_query=saved.get('query','')
        self.library_type='saved'
        self.input_open=False
        self.settings=False
        self.buttons=[]
        self.request=None
        self.results=None
        self.scroll=0
        self.history=[]
        self.loading=False
        self.error=''
        self.notice=''
        self.notice_until=0
        self.auth_ok=True
        self.control_busy=False
        self.context=None
        self.last_cover=None
        self.cover=None
        self.cover_generation=0
        self.last_tap=0
        self.closed=False
        self.read_only=False
        self.shown_second=-1

    def persist(self):
        atomic_json(self.config,{'orientation':self.orientation,'query':self.search_query})

    def load(self,kind=None,offset=0,query=None,identifier='',fresh=False,preserve=False):
        kind=kind or (('search:'+self.search_type) if self.page=='search' else self.library_type if self.page=='saved' else 'playlists')
        query=(self.search_query if kind.startswith('search:') else '') if query is None else query
        generation=self.workers.generation+1
        params=dict(kind=kind,query=query,offset=offset,identifier=identifier,fresh=fresh)
        previous=self.workers.generation
        self.workers.generation=generation
        try:self.workers.submit(('page',generation,params))
        except BackendError as error:
            self.workers.generation=previous
            self.toast(str(error));return
        self.request=params
        self.loading=True;self.error='';self.scroll=0
        if not preserve:self.results=None

    def toast(self,message):
        self.notice=message;self.notice_until=time.monotonic()+5

    def tick(self):
        changed=False
        while True:
            try:kind,generation,value,error=self.workers.results.get_nowait()
            except queue.Empty:break
            changed=True
            if kind=='page' and generation==self.workers.generation:
                self.loading=False;self.results=value;self.error=error or ''
                if value:self.scroll=0
            elif kind=='control':
                self.control_busy=False
                if value:self.state=value
                if error:self.toast(error)
            elif kind=='auth':self.auth_ok=bool(value)
            elif kind=='art' and generation==self.cover_generation:
                if value is not None:self.cover=value
        item=self.state.get('item') or {}
        uri=item.get('uri')
        if uri!=self.last_cover:
            self.last_cover=uri;self.cover=None;self.cover_generation+=1
            covers=((item.get('decorations') or {}).get('visual_identity') or {}).get('cover') or []
            url=cover_url(covers)
            if url:
                try:self.workers.submit(('art',self.cover_generation,url))
                except BackendError:pass
        second=int(time.time())
        if self.page=='now' and second!=self.shown_second:
            self.shown_second=second;changed=True
        if self.notice and time.monotonic()>self.notice_until:
            self.notice='';changed=True
        return changed

    def snapshot(self):
        return {'api_connected':self.auth_ok,'loading':self.loading,'row_count':len(self.results['rows']) if self.results else 0,
                'kind':self.request['kind'] if self.request else None,'control_busy':self.control_busy,'artwork_loaded':self.cover is not None,
                'artwork_pixels':list(self.cover.size) if self.cover is not None else None}

    def swipe(self,origin,end):
        if self.settings or self.input_open:return
        dx,dy=end[0]-origin[0],end[1]-origin[1]
        if self.page=='now' and 568<=origin[1]<=626 and abs(dx)>30:
            self.dispatch(('seek-bar',),end[0]);return
        if self.page=='now' or not 265<=origin[1]<=725 or abs(dy)<60 or abs(dy)<abs(dx)*1.5 or not self.results or self.loading:return
        if dy<0:
            if self.scroll+5<len(self.results['rows']):self.dispatch(('scroll',5))
            elif self.results.get('next_offset') is not None or self.results.get('next_cursor'):self.dispatch(('next',))
        elif self.scroll>0:self.dispatch(('scroll',-5))
        elif self.request and self.request.get('offset',0)>0:self.dispatch(('previous',))

    def close(self):
        if self.closed:return
        self.closed=True;self.workers.close();self.keyboard.close()
        self.persist()

    def command(self,command,value=None):
        if self.control_busy:self.toast('再生操作を実行中です');return
        try:self.workers.controls.put_nowait((command,value));self.control_busy=True
        except queue.Full:self.toast('操作を減らして再度お試しください')

    def duration(self):
        value=(((self.state.get('item') or {}).get('decorations') or {}).get('playback') or {}).get('duration_ms') or 0
        return value if isinstance(value,(int,float)) else 0

    def position(self):
        position=self.state.get('position') or {}
        value=position.get('position_ms',0)
        if self.state.get('status')=='playing':
            stamp=position.get('timestamp_ms',time.time()*1000)
            value+=max(0,min(86400000,time.time()*1000-stamp))*position.get('speed',1)
        return max(0,min(self.duration() or value,value))

    def render(self):
        self.buttons=[]
        image=Image.new('RGBA',(600,1024),surface.BG)
        draw=ImageDraw.Draw(image)
        def label(x,y,value,size=22,color=surface.FG,width=550):
            draw.text((x,y),self.fitted(value,size,width),font=self.fonts[size],fill=color)
        def button(bounds,value,action,enabled=True,selected=False):
            draw.rounded_rectangle(bounds,radius=10,fill='#245044' if selected else '#253341' if enabled else '#17212b')
            label(bounds[0]+10,bounds[1]+12,value,18,surface.GREEN if selected else surface.FG if enabled else '#687583',bounds[2]-bounds[0]-16)
            if enabled:self.buttons.append((bounds,action))
        def icon_button(bounds,icon,action,enabled=True,selected=False,caption=None):
            # Vector shapes avoid missing media glyphs in the Japanese font.
            x0,y0,x1,y1=bounds
            cx=(x0+x1)//2;cy=(y0+y1)//2-(10 if caption else 0)
            color=surface.GREEN if selected and enabled else surface.FG if enabled else '#687583'
            draw.rounded_rectangle(bounds,radius=18,fill='#245044' if selected else '#253341' if enabled else '#17212b')
            if icon=='play':
                draw.polygon([(cx-11,cy-19),(cx-11,cy+19),(cx+20,cy)],fill=color)
            elif icon=='pause':
                for dx in (-14,5):draw.rectangle((cx+dx,cy-18,cx+dx+8,cy+18),fill=color)
            elif icon in ('prev','next'):
                direction=1 if icon=='next' else -1
                draw.polygon([(cx-14*direction,cy-18),(cx-14*direction,cy+18),(cx+12*direction,cy)],fill=color)
                bx=cx+18*direction
                draw.rectangle((bx-3,cy-18,bx+3,cy+18),fill=color)
            elif icon=='shuffle':
                draw.line([(cx-23,cy-14),(cx-12,cy-14),(cx+12,cy+14),(cx+23,cy+14)],fill=color,width=4)
                draw.line([(cx-23,cy+14),(cx-12,cy+14),(cx+12,cy-14),(cx+23,cy-14)],fill=color,width=4)
                for dy in (-14,14):draw.polygon([(cx+18,cy+dy-7),(cx+28,cy+dy),(cx+18,cy+dy+7)],fill=color)
            elif icon=='repeat':
                draw.line([(cx-22,cy+3),(cx-22,cy-12),(cx+20,cy-12)],fill=color,width=4)
                draw.line([(cx+22,cy-3),(cx+22,cy+12),(cx-20,cy+12)],fill=color,width=4)
                draw.polygon([(cx+15,cy-19),(cx+26,cy-12),(cx+15,cy-5)],fill=color)
                draw.polygon([(cx-15,cy+5),(cx-26,cy+12),(cx-15,cy+19)],fill=color)
            if caption:
                box=draw.textbbox((0,0),caption,font=self.fonts[18])
                draw.text((cx-(box[2]-box[0])/2,y1-28),caption,font=self.fonts[18],fill=color)
            if enabled:self.buttons.append((bounds,action))
        self._button=button
        label(18,10,'Spotify',28,surface.GREEN,190)
        if self.history:button((206,4,290,58),'戻る',('back',))
        button((298,4,392,58),'設定',('settings',))
        label(406,18,'縦固定',18,surface.DIM,86)
        button((502,4,596,58),'閉じる',('exit',))
        try:route=Path('/run/jtb1-soloist/output').read_text().strip()
        except OSError:route=''
        route_name={'headphones':'イヤホン','speaker':'スピーカー'}.get(route,'音声接続待ち')
        volume=self.state.get('volume',0)
        volume_limit=Player().volume_limit()
        label(18,66,route_name+' / 音量 '+str(volume)+'% / '+('API接続済み' if self.auth_ok else 'API再接続待ち'),18,surface.DIM)
        if self.notice:label(18,94,self.notice,18,'#ffcf83')
        if self.page=='now':
            decorations=(self.state.get('item') or {}).get('decorations') or {}
            if self.cover:
                image.alpha_composite(self.cover,((600-self.cover.width)//2,142))
            else:
                draw.ellipse((180,152,420,392),fill='#203d39',outline=surface.GREEN,width=4)
                draw.ellipse((268,240,332,304),fill=surface.BG)
            label(22,438,(decorations.get('identity') or {}).get('name','曲を選んで再生してください'),28)
            creators=decorations.get('creators') or []
            artist=' / '.join((((x.get('entity') or {}).get('decorations') or {}).get('identity') or {}).get('name','') for x in creators)
            label(22,488,artist,22,surface.DIM)
            pos=self.position();duration=self.duration()
            def minutes(ms):return str(int(ms)//60000)+':'+str(int(ms)//1000%60).zfill(2)
            label(22,542,minutes(pos)+' / '+minutes(duration),18,surface.DIM)
            draw.rounded_rectangle((22,586,578,598),radius=6,fill='#35414e')
            if duration:draw.rounded_rectangle((22,586,22+max(12,556*pos/duration),598),radius=6,fill=surface.GREEN)
            self.buttons.append(((20,568,580,626),('seek-bar',)))
            icon_button((24,646,190,716),'prev',('control','prev',None),not self.control_busy)
            icon_button((214,646,386,716),'pause' if self.state.get('status')=='playing' else 'play',('toggle',),not self.control_busy,True)
            icon_button((410,646,576,716),'next',('control','next',None),not self.control_busy)
            options=self.state.get('options') or {}
            shuffle=bool(options.get('shuffle'))
            icon_button((24,742,288,824),'shuffle',('shuffle',),not self.control_busy,shuffle,'ON' if shuffle else 'OFF')
            repeat=options.get('repeat','off')
            icon_button((310,742,576,824),'repeat',('repeat',),not self.control_busy,repeat in ('context','track'),{'off':'OFF','context':'全体','track':'1曲'}.get(repeat,'OFF'))
        else:
            if self.page=='search':
                button((18,126,475,186),self.keyboard.value or '曲名、歌手名を入力',('input',))
                button((485,126,584,186),'検索',('submit',),True,True)
                for i,(kind,name) in enumerate([('track','曲'),('album','アルバム'),('artist','歌手'),('playlist','リスト')]):
                    button((10+i*148,200,148+i*148,250),name,('type',kind),True,kind==self.search_type)
            elif self.page=='saved':
                label(20,130,'ライブラリー',28)
                for i,(kind,name) in enumerate([('saved','保存曲'),('albums','アルバム'),('artists','歌手')]):
                    button((12+i*196,196,198+i*196,248),name,('library',kind),True,kind==self.library_type)
            elif self.page=='playlists':label(20,150,'プレイリスト',28)
            elif self.context:
                label(20,130,self.context['title'],28)
                button((18,190,580,248),'この'+{'playlist':'リスト','album':'アルバム','artist':'歌手'}.get(self.context['kind'],'対象')+'を再生',('context-play',),not self.control_busy,True)
            if self.loading:label(22,268,'取得中…',22,surface.GREEN)
            elif self.error:
                label(22,270,self.error,18,'#ffcf83')
                if self.context:label(22,312,'内容を取得できなくても上のボタンで再生できます',18,surface.DIM)
            elif self.results is not None:
                rows=self.results['rows'];visible=3 if self.input_open else 5
                for index,row in enumerate(rows[self.scroll:self.scroll+visible]):
                    y=268+index*86
                    button((16,y,584,y+78),row['title'],('row',self.scroll+index),row.get('playable',True))
                    label(28,y+43,row['subtitle'],18,surface.DIM,530)
                if not rows:label(22,286,'曲や項目がありません',22,surface.DIM)
                if not self.input_open:
                    offset=self.results.get('offset',0)
                    total=self.results.get('total')
                    label(22,710,str(offset+self.scroll+1 if rows else 0)+'件目から表示'+(' / '+str(total)+'件' if total is not None and self.page!='search' else ''),18,surface.DIM)
            elif self.page=='search':label(22,280,'日本語または英数字で検索できます',22,surface.DIM)
            if not self.input_open:
                for i,(name,action,enabled) in enumerate([
                    ('前頁',('previous',),bool(self.request and self.request.get('offset',0)>0)),
                    ('上へ',('scroll',-5),self.scroll>0),
                    ('下へ',('scroll',5),bool(self.results and self.scroll+5<len(self.results['rows']))),
                    ('次頁',('next',),bool(self.results and (self.results.get('next_offset') is not None or self.results.get('next_cursor')))),
                    ('更新',('reload',),self.request is not None)]):
                    button((8+i*118,754,118+i*118,812),name,action,enabled and not self.loading)
                label(22,850,'曲をタップすると再生、リストやアルバムは内容を表示',18,surface.DIM)
        if self.input_open:self.draw_keyboard(draw,label,button)
        for i,(name,page) in enumerate([('再生中','now'),('検索','search'),('保存曲','saved'),('リスト','playlists')]):
            button((i*150+3,930,i*150+147,1020),name,('tab',page),True,self.page==page)
        if self.settings:
            draw.rounded_rectangle((25,145,575,895),radius=20,fill='#202d39',outline=surface.GREEN,width=2)
            self.buttons.append(((25,145,575,895),('noop',)))
            label(52,174,'音量と表示',28)
            label(52,235,'現在の音量：'+str(volume)+'%',28)
            button((50,302,284,370),'音量 −5%',('volume',-5),not self.control_busy)
            button((312,302,550,370),'音量 ＋5%',('volume',5),not self.control_busy and isinstance(volume,int) and volume<volume_limit)
            label(52,398,'出力先：'+route_name,22,surface.FG,490)
            button((50,480,550,542),'現在の曲を一時停止',('control','pause',None))
            label(52,582,'再生エンジン：公式Soloist',22)
            label(52,628,'音質は検証済みの既存設定を維持します',18,surface.DIM,490)
            label(52,676,'日本語変換：SKK辞書（GPL-2.0以降）',18,surface.DIM,490)
            button((50,790,550,850),'設定を閉じる',('settings',),True,True)
        if self.marker and time.monotonic()<self.marker[2]:
            x,y=self.marker[:2];draw.ellipse((x-8,y-8,x+8,y+8),outline=surface.GREEN,width=2)
        return image

    def draw_keyboard(self,draw,label,button):
        draw.rectangle((0,520,600,927),fill='#101c26')
        values=self.keyboard.candidates()
        start=self.keyboard.candidate_page*3
        for i,value in enumerate(values[start:start+3]):
            button((4+i*154,526,152+i*154,579),value,('candidate',value),True,True)
        button((469,526,596,579),'次の候補',('candidates',),len(values)>3)
        rows=KANA if self.keyboard.mode in ('kana','katakana') else ASCII
        for row,letters in enumerate(rows):
            if self.keyboard.mode=='katakana':letters=katakana(letters)
            elif self.keyboard.mode=='upper':letters=letters.upper()
            width=600/len(letters)
            for col,char in enumerate(letters):
                button((int(col*width)+2,590+row*54,int((col+1)*width)-2,640+row*54),char,('char',char))
        controls=[('あ/ア/a/A',('mode',)),('濁点',('voiced',)),('小字',('small',)),('空白',('space',)),('削除',('delete',)),('完了',('input-close',))]
        for i,(label_text,action) in enumerate(controls):button((2+i*100,866,98+i*100,922),label_text,action)

    def tap(self,x,y):
        now=time.monotonic()
        if now-self.last_tap<0.16:return True
        self.last_tap=now;self.marker=(x,y,now+0.5)
        for bounds,action in reversed(self.buttons):
            if bounds[0]<=x<=bounds[2] and bounds[1]<=y<=bounds[3]:
                try:return self.dispatch(action,x)
                except BackendError as error:self.toast(str(error));return True
        return True

    def dispatch(self,action,x=0):
        kind=action[0]
        if kind=='exit':self.close();return False
        if kind=='settings':self.settings=not self.settings
        elif kind=='flip':pass  # Rotation is owned by the portrait system shell.
        elif kind=='tab':
            self.page=action[1];self.history=[];self.context=None;self.settings=False;self.input_open=False;self.results=None;self.request=None;self.error=''
            self.workers.generation+=1
            if self.page not in ('now','search'):self.load()
            elif self.page=='search' and self.search_query:self.load()
        elif kind=='input':self.input_open=True
        elif kind=='input-close':self.keyboard.commit();self.input_open=False
        elif kind=='char':self.keyboard.insert(action[1])
        elif kind=='mode':
            modes=['kana','katakana','ascii','upper'];self.keyboard.mode=modes[(modes.index(self.keyboard.mode)+1)%4]
        elif kind=='voiced':self.keyboard.change_last(VOICED)
        elif kind=='small':self.keyboard.change_last(dict(SMALL,**{v:k for k,v in SMALL.items()}))
        elif kind=='space':self.keyboard.commit();self.keyboard.committed+=' '
        elif kind=='delete':self.keyboard.backspace()
        elif kind=='candidate':self.keyboard.commit(action[1])
        elif kind=='candidates':self.keyboard.candidate_page=(self.keyboard.candidate_page+1)%max(1,(len(self.keyboard.candidates())+2)//3)
        elif kind=='submit':
            self.keyboard.commit();self.search_query=self.keyboard.value.strip()
            if not self.search_query:raise BackendError('検索語を入力してください')
            self.input_open=False;self.persist();self.load()
        elif kind=='type':
            self.search_type=action[1]
            if self.search_query:self.load()
        elif kind=='library':self.library_type=action[1];self.load()
        elif kind=='scroll':self.scroll=max(0,min(max(0,len(self.results['rows'])-5),self.scroll+action[1]))
        elif kind in ('next','previous','reload'):
            if not self.request:return True
            params=dict(self.request)
            if kind=='next':
                if self.results.get('next_cursor'):params['query']=self.results['next_cursor']
                else:params['offset']=self.results.get('next_offset') or 0
            elif kind=='previous':params['offset']=max(0,params['offset']-10)
            else:params['fresh']=True
            self.load(**params)
        elif kind=='row':
            row=self.results['rows'][action[1]]
            if row['kind'] in ('playlist','album','artist'):
                self.history.append((self.page,self.request,self.results,self.scroll,self.context))
                self.context=row;self.page='detail';self.input_open=False
                self.load(row['kind'],identifier=row['id'],query='')
            else:self.command('play',row['uri'])
        elif kind=='back' and self.history:
            self.page,self.request,self.results,self.scroll,self.context=self.history.pop();self.loading=False;self.error='';self.workers.generation+=1
        elif kind=='context-play':self.command('play',self.context['uri'])
        elif kind=='toggle':self.command('pause' if self.state.get('status')=='playing' else 'play')
        elif kind=='control':self.command(action[1],action[2])
        elif kind=='volume':self.command('volume',max(0,min(Player().volume_limit(),int(self.state.get('volume',0))+action[1])))
        elif kind=='seek-bar' and self.duration():self.command('seek',int(self.duration()*max(0,min(1,(x-22)/556))))
        elif kind=='shuffle':self.command('shuffle','off' if (self.state.get('options') or {}).get('shuffle') else 'on')
        elif kind=='repeat':
            modes=['off','context','track'];current=(self.state.get('options') or {}).get('repeat','off');self.command('repeat',modes[(modes.index(current)+1)%3])
        return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--session',required=True)
    parser.add_argument('--duration',type=int,default=2147483647)
    args=parser.parse_args()
    surface.RUN=Path('/run/jtb1-gui')
    holder=[]
    def factory(font,catalog):
        view=LiveView(font);holder.append(view);return view
    try:surface.run(surface.BASE/'share/NotoSansCJK-Regular.ttc',surface.BASE/'data/empty.json',args.duration,args.session,view_factory=factory)
    finally:
        for view in holder:view.close();view.workers.join()


if __name__=='__main__':
    try:main()
    except Exception:
        print('GUI stopped; details suppressed for privacy.',flush=True)
        raise SystemExit(1)
