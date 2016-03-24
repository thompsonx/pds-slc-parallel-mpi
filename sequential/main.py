from syncedtracelog import SyncedTraceLog
import sys
import os.path
import time

def main():
    if len(sys.argv) != 4:
        print "Missing argument!\nArguments:\n1 - file path to a *.kth\n2 - minimal event difference [ns]\n3 - minimum message delay between 2 processes [ns]"
        return
    
    exec_start = time.time()
    
    st = SyncedTraceLog(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), True, True)
    
    execution_time = time.time() - exec_start
    print "Execution time: {0}".format(execution_time)
    
    path = os.path.split(sys.argv[1])[0]
    if path != '':
        path += "/"
                
    st.export_to_file(path + "synchronized_trace.kst")
    
    

if __name__ == "__main__":
    main()