"""Authenticated AES-256-GCM framed backup stream, no plaintext temporary file."""
import base64,os,struct,sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
MAGIC=b'EHSAN-BACKUP-v1\x00'
key=base64.b64decode(os.environ['BACKUP_ENCRYPTION_KEY'],validate=True)
if len(key)!=32:raise SystemExit('BACKUP_ENCRYPTION_KEY must encode 32 bytes')
aes=AESGCM(key);source=sys.stdin.buffer;target=sys.stdout.buffer
if sys.argv[1]=='encrypt':
    target.write(MAGIC);counter=0
    while True:
        data=source.read(1024*1024);final=not data;nonce=os.urandom(12)
        cipher=aes.encrypt(nonce,data,MAGIC+struct.pack('>Q?',counter,final))
        target.write(struct.pack('>I?',len(cipher),final)+nonce+cipher);counter+=1
        if final:break
elif sys.argv[1]=='decrypt':
    if source.read(len(MAGIC))!=MAGIC:raise SystemExit('Invalid backup header')
    counter=0
    while True:
        header=source.read(5)
        if len(header)!=5:raise SystemExit('Truncated backup')
        length,final=struct.unpack('>I?',header)
        if length<16 or length>1024*1024+16:raise SystemExit('Invalid backup frame')
        nonce=source.read(12);cipher=source.read(length)
        plain=aes.decrypt(nonce,cipher,MAGIC+struct.pack('>Q?',counter,final));target.write(plain);counter+=1
        if final:
            if source.read(1):raise SystemExit('Trailing backup data')
            break
else:raise SystemExit('encrypt|decrypt required')
