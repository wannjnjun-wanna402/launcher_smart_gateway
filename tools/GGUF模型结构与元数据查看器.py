import struct, sys, os

def read_gguf_metadata(path):
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != b'GGUF':
            return None
        version = struct.unpack('<I', f.read(4))[0]
        n_tensors = struct.unpack('<Q', f.read(8))[0]
        n_kv = struct.unpack('<Q', f.read(8))[0]
        kv = {}
        for _ in range(n_kv):
            klen = struct.unpack('<Q', f.read(8))[0]
            key = f.read(klen).decode('utf-8', 'replace')
            vtype = struct.unpack('<I', f.read(4))[0]
            val = None
            if vtype == 0:   # uint8
                val = struct.unpack('<B', f.read(1))[0]
            elif vtype == 1: # int8
                val = struct.unpack('<b', f.read(1))[0]
            elif vtype == 2: # uint16
                val = struct.unpack('<H', f.read(2))[0]
            elif vtype == 3: # int16
                val = struct.unpack('<h', f.read(2))[0]
            elif vtype == 4: # uint32
                val = struct.unpack('<I', f.read(4))[0]
            elif vtype == 5: # int32
                val = struct.unpack('<i', f.read(4))[0]
            elif vtype == 6: # float32
                val = struct.unpack('<f', f.read(4))[0]
            elif vtype == 7: # bool
                val = struct.unpack('<B', f.read(1))[0] != 0
            elif vtype == 8: # string
                slen = struct.unpack('<Q', f.read(8))[0]
                val = f.read(slen).decode('utf-8', 'replace')
            elif vtype == 9: # array
                atype = struct.unpack('<I', f.read(4))[0]
                acount = struct.unpack('<Q', f.read(8))[0]
                # for token array we only need count if type is string
                if atype == 8:
                    # skip strings, capture count as proxy for vocab size
                    for _ in range(acount):
                        slen = struct.unpack('<Q', f.read(8))[0]
                        f.read(slen)
                    val = f'ARRAY<str>[len={acount}]'
                else:
                    esize = {0:1,1:1,2:2,3:2,4:4,5:4,6:4,10:8,11:8}.get(atype, 4)
                    f.read(esize * acount)
                    val = f'ARRAY[type={atype},count={acount}]'
            elif vtype == 10: # uint64
                val = struct.unpack('<Q', f.read(8))[0]
            elif vtype == 11: # int64
                val = struct.unpack('<q', f.read(8))[0]
            elif vtype == 12: # float64
                val = struct.unpack('<d', f.read(8))[0]
            else:
                val = f'<unknown type {vtype}>'
            kv[key] = val
        return {'version': version, 'n_tensors': n_tensors, 'kv': kv}

if __name__ == '__main__':
    models = sys.argv[1:]
    for m in models:
        info = read_gguf_metadata(m)
        if not info:
            print(f'[SKIP] {os.path.basename(m)}: not a GGUF')
            continue
        kv = info['kv']
        vocab = kv.get('tokenizer.ggml.vocab_size')
        tmodel = kv.get('tokenizer.ggml.model')
        arch = kv.get('general.architecture')
        name = kv.get('general.name')
        # try to find token array length as fallback
        tok_arr = None
        for k, v in kv.items():
            if k == 'tokenizer.ggml.tokens' and isinstance(v, str) and v.startswith('ARRAY<str>'):
                tok_arr = v
        print(f"=== {os.path.basename(m)} ===")
        print(f"  general.name      : {name}")
        print(f"  architecture      : {arch}")
        print(f"  tokenizer.model   : {tmodel}")
        print(f"  vocab_size        : {vocab}")
        if tok_arr:
            print(f"  tokens array      : {tok_arr}")
        print()
