import ctypes, json, os, time
OUT="/home/admin/aletheia-leak-20261010"
class MI(ctypes.Structure): _fields_=[(n,ctypes.c_size_t) for n in "arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost".split()]
libc=ctypes.CDLL("libc.so.6"); libc.mallinfo2.restype=MI
def st():
    d={}
    for l in open("/proc/self/status"):
        if l.split(":")[0] in ("VmRSS","RssAnon"): d[l.split(":")[0]]=int(l.split()[1])//1024
    mi=libc.mallinfo2(); d.update(arena=mi.arena>>20, in_use=mi.uordblks>>20, free_held=mi.fordblks>>20)
    return d
b=st(); t=time.time(); rc=libc.malloc_trim(0); dt=round(time.time()-t,3); a=st()
json.dump({"before":b,"after":a,"malloc_trim_rc":rc,"secs":dt,"time":time.strftime("%F %T")},open(OUT+"/trim_result.json","w"),indent=1)
