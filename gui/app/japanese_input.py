"""Small offline Japanese/ASCII keyboard. SKK candidate lookup uses SQLite.
No input text is sent to a third-party IME service.
"""
import json
import sqlite3
from pathlib import Path

KANA = ['あいうえおかきくけこ','さしすせそたちつてと','なにぬねのはひふへほ','まみむめもやゆよらり','るれろわをんーっゃゅょ']
ASCII = ['1234567890','qwertyuiop','asdfghjkl-','zxcvbnm,.:','/@_!()[]? ']
SMALL = dict(zip('あいうえおつやゆよわかけ','ぁぃぅぇぉっゃゅょゎゕゖ'))
VOICED = dict(zip('かきくけこさしすせそたちつてとはひふへほう','がぎぐげござじずぜぞだぢづでどばびぶべぼゔ'))
VOICED.update(dict(zip('ばびぶべぼ','ぱぴぷぺぽ')))
VOICED.update(dict(zip('ぱぴぷぺぽ','はひふへほ')))
VOICED.update({v:k for k,v in list(VOICED.items()) if k not in 'ばびぶべぼぱぴぷぺぽはひふへほ'})


def katakana(value):
    return ''.join(chr(ord(c)+0x60) if 'ぁ'<=c<='ゖ' else c for c in value)


class Keyboard:
    def __init__(self, dictionary=None):
        self.committed=''
        self.pending=''
        self.mode='kana'
        self.candidate_page=0
        self.database=None
        if dictionary and Path(dictionary).exists():
            self.database=sqlite3.connect('file:'+str(dictionary)+'?mode=ro',uri=True)

    @property
    def value(self):return (self.committed+self.pending)[:200]

    def set(self,value):
        self.committed=value[:200];self.pending='';self.candidate_page=0

    def candidates(self):
        if not self.pending:return []
        values=[self.pending,katakana(self.pending)]
        if self.database:
            row=self.database.execute('SELECT candidates FROM words WHERE reading=?',(self.pending,)).fetchone()
            if row:values+=json.loads(row[0])
        return list(dict.fromkeys(values))

    def insert(self,value):
        if len(self.value)<200:self.pending+=value
        self.candidate_page=0

    def commit(self,value=None):
        self.committed=(self.committed+(self.pending if value is None else value))[:200]
        self.pending='';self.candidate_page=0

    def backspace(self):
        if self.pending:self.pending=self.pending[:-1]
        else:self.committed=self.committed[:-1]
        self.candidate_page=0

    def change_last(self,mapping):
        target=self.pending or self.committed
        if target:
            transformed=target[:-1]+mapping.get(target[-1],target[-1])
            if self.pending:self.pending=transformed
            else:self.committed=transformed
        self.candidate_page=0

    def close(self):
        if self.database:self.database.close();self.database=None
