import sys
import os
import os.path
import tracelog as tr
from shutil import copyfile
from mpi4py import MPI
from paralleltrace import ParallelSyncedTrace

def whoiam(rank, data):
    print "I am {0}. I have {1}.".format(rank, data)


def main():
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()
    
    if len(sys.argv) != 4:
        if rank == 0:
            print "Missing argument!\nArguments:\n1 - file path to a *.kth\n2 - minimal event difference [ns]\n3 - minimum message delay between 2 processes [ns]"
        return
        
    # Load *.kth and distribute information inside
    if rank == 0:
        filename = sys.argv[1]
        cleanname = os.path.split(filename)[1]
        path = os.path.split(filename)[0]
        newfolder = "synchronized"
        if path != "":
            newfolder = path + "/" + newfolder
        if not os.path.exists(newfolder):
            os.makedirs(newfolder)
        copyfile(filename, newfolder + "/" + cleanname)
        data = (tr.read_header(filename), tr.trim_filename_suffix(filename), 
                newfolder)
    else:
        data = None
    data = comm.bcast(data, root = 0)
    
    # Init trace
    tracedata, tracefile = tr.read_trace(data[1], rank)
    newfolder = data[2]
    trace = ParallelSyncedTrace(tracedata, rank, data[0], int(sys.argv[2]), 
                                int(sys.argv[3]), False, False, comm)
    
    # Make an init time of the process with the lowest init time as reference
    # time for all events in all processes
    init_time = trace.get_init_time()
    data = init_time
    data = comm.gather(data, root=0)
    
    if rank == 0:
        starttime = min(data)
        offsets = []
        for time in data:
            offsets.append(time - starttime)
    else:
        offsets = None
    
    data = comm.scatter(offsets, root=0)
    trace.time_offset = data
    
    while not trace.is_pointer_at_end():
        trace.process_event()
        
    trace.export_data(newfolder + "/" + os.path.split(tracefile)[1])

if __name__ == "__main__":
    main()