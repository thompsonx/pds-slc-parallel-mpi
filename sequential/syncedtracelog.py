#
#    Copyright (C) 2016 Tomas Panoc
#

import copy 
from tracelog import TraceLog, Trace
from Queue import Queue
from collections import OrderedDict
from cStringIO import StringIO
           
class SyncedTraceLog (TraceLog):
    
    def __init__(self, filename, *settings):
        """ Creates new SyncedTraceLog object, different method is used 
            according to passed argument.
            
            Arguments: 
                filename -- a path to a tracelog file (*.kth) 
                settings -- tuple( min_event_diff, min_msg_delay, 
                                        forward_amort, backward_amort ) 
                min_event_diff -- Minimal difference between 2 events 
                                    in a process (nanoseconds)
                min_msg_delay -- Minimum message delay of messages from 
                                    one process to another (nanoseconds)
                forward_amort -- True/False, turns on/off forward amortization
                                    feature
                backward_amort -- True/False, turns on/off backward 
                                    amortization feature
                Creates a new SyncedTraceLog object from an existing TraceLog 
                object and does the synchronization
        """
        
        
        TraceLog.__init__(self, filename)
        self._syncing = True         
        self._init(settings)
    
    
    def _init(self, settings):
            # Matrix of unprocessed sent messages        
            self.messages = [[SQueue() for x in range(self.process_count)] for x in range(self.process_count)] 
            
            self.minimal_event_diff = settings[0]
            self.minimum_msg_delay = settings[1]
            self.forward_amort = settings[2]
            self.backward_amort = settings[3]
            
            self.straces = []
            messenger = Messenger(self)
            for t in self.traces:
                strace = SyncedTrace(t.data, t.process_id, self.pointer_size, \
                                     self.minimal_event_diff, \
                                     self.minimum_msg_delay, \
                                     self.forward_amort, \
                                     self.backward_amort, \
                                     self.messages, \
                                     messenger)
                self.straces.append(strace)
            self.traces = self.straces
                                               
            self._synchronize()
            
        
           
    def _synchronize(self):
        """ Main feature of this class. It controls whole synchronization 
            procedure 
        """
        
        # Make an init time of the process with the lowest init time reference
        # time for all events from all processes
        starttime = min([ trace.get_init_time() for trace in self.traces ])
        for trace in self.traces:
            trace.time_offset = trace.get_init_time() - starttime
#             trace.set_init_time(trace.time_offset)
        
        # List of unprocessed processes
        processes = [x for x in range(self.process_count)]
        # A process which will be processed
        current_p = processes[0]
        
        # Control algorithm goes through every event of a process,
        # it jumps to another process if a send event of reached receive event
        # is found to be unprocessed or if the end of process is reached
        while processes:
            
            working_p = current_p
            trace = self.traces[working_p]
            
            while working_p == current_p:
                if trace.get_next_event_time() is not None:
                    if trace.get_next_event_name() == "Recv ":
                        sender = trace.get_msg_sender()
                        if self.messages[sender][current_p].empty() is False:
                            if self.backward_amort:
                                #Backward amortization - check refilled receive times
                                if not trace.are_receive_times_refilled():
                                    current_p = trace.get_missing_receive_time_process_id()
                                    break
                                
                                trace.process_event()
                                #Backward amortization - add receive time and maximum offset
                                self.traces[sender].refill_received_time(trace.get_last_received_sent_time(),\
                                                                         trace.get_last_receive_event_time(),\
                                                                         working_p) 
                            else:
                                trace.process_event()                                        
                        else:
                            current_p = sender
                    else:
                        trace.process_event()
                else:
                    processes.remove(current_p)
                    #List is empty, stops the loop
                    if not processes:
                        current_p += 1
                    else:
                        current_p = processes[0]
        
                
    def refill_received_time(self, target, send_time, receive_time, receiver, \
                            new_record=True):
        """ Pairs a receive time to the corresponding sent time and computes 
            maximum offset. Works only if the backward amortization is turned
            on.
            
            Arguments:
            target -- ID of a process whose data should be refilled
            send_time -- time of a corresponding send event
            receive_time -- time of a receipt of the msg to be filled
            new_record -- if True you are adding missing receive time otherwise \
                            you are updating an existing receive time
        """
        if self._syncing:
            self.traces[target].refill_received_time(send_time, receive_time, \
                                                    receiver, new_record)
    
    def export_to_file(self, filename):
        """ Saves synchronized tracelog into a file 
            
            Arguments:
            filename -- Path to a *.kst
        """
        data = str(self.pointer_size) + '\n' + str(self.process_count) + '\n'
        
        traces = ""

        for t in self.traces:
            tdata = t.export_data()
            data += str(len(tdata)) + '\n'
            traces += tdata
        
        data += traces
        
        with open(self.filename, "r") as f:
            f.readline()
            data += f.read()
            
        with open(filename, "wb") as f:
            f.write(data)

      
class SyncedTrace(Trace):
    
    def __init__(self, data, process_id, pointer_size, minimal_event_diff, \
                                     minimum_msg_delay, \
                                     forward_amort, \
                                     backward_amort, \
                                     messages, \
                                     messenger):
        """ Synchronizes events of one process.
        
            Arguments:
            data -- content of a process's *.ktt file
            process_id -- ID of the process
            pointer_size -- 4 or 8, type of binary data within the *.ktt file
            minimal_event_diff -- see the SyncedTraceLog class
            minimum_msg_delay -- see the SyncedTraceLog class
            forward_amort -- see the SyncedTraceLog class
            backward_amort -- see the SyncedTraceLog class
            messages -- shared variable among SyncedTraces, 2-dimensional array
                        of Queues, first coordinate is a sender of message,
                        second is the recipient, Queues stores sent times.
            messenger -- an object of the Messenger class, communicator between
                            a SyncedTrace and a SyncedTraceLog
        """
        Trace.__init__(self, data, process_id, pointer_size)
        self._minimal_event_diff = minimal_event_diff
        self._minimum_msg_delay = minimum_msg_delay
        self._forward_amort = forward_amort
        self._backward_amort = backward_amort
        self._messages = messages
        self._messenger = messenger
        self._data_list = []
        self._header_info = self.data[:self.pointer]
        self._last_event_time = 0
        self._send_events = OrderedDict()
        self._last_received_sent_time = 0
        self._last_refilled_send_time = None
        self._last_receive_event_time = 0
        self._missing_receive_time_process_id = None
        self._is_backward_amortization = False
        self._receive_send_table = {}
        
    def _clock_check(self, time, start_pointer, end_pointer=False, \
                     is_receive=False, sent_time=0):
        """ Checks, computes and repairs an event timestamp
            
            Arguments:
            time -- a timestamp to be checked
            start_pointer -- a pointer value before an event unpacking/reading
            end_pointer -- a pointer value after the event unpacking/reading, 
                            if False self.pointer is used
            is_receive -- marks a receive event
            sent_time -- a timestamp of corresponding send event
         """
        newtime = 0
        
        if not is_receive:
            newtime = self._clock(time + self.time_offset)
        else:
            newtime = self._clock_receive(time + self.time_offset, sent_time)
        
        # Save time to the data list
        self._repair_time(newtime, start_pointer, end_pointer)
        
        return newtime            
    
    def _clock(self, time):
        """ Computes a new time for a process's internal event 
            
            Arguments:
            time -- the time to be fixed
        """
        newtime = 0
        if self._last_event_time != 0:
            newtime = max([time, self._last_event_time + \
                           self._minimal_event_diff])
        else:
            newtime = time
        
        self._last_event_time = newtime
        
        return newtime
    
    def _clock_receive(self, time, sent_time):
        """ Computes a new time for a process's receive event 
            
            Arguments:
            time -- the time to be fixed
            sent_time -- time of the corresponding send event
        """
        newtime = 0
        if self._last_event_time != 0:
            newtime = max([sent_time + self._minimum_msg_delay, time, \
                           self._last_event_time + \
                           self._minimal_event_diff])
        else:
            newtime = max([sent_time + self._minimum_msg_delay, time])
        
        if self._forward_amort:
            self._forward_amortization(time, newtime)
        if self._is_backward_amortization:
            self._backward_amortization(time, newtime)
        
        self._last_event_time = newtime
        self._last_receive_event_time = newtime
        
        return newtime
        
    def _forward_amortization(self, origin_time, new_time):
        """ Checks shift of a receive event. If a shift exists the time offset 
            is increased to keep the spacing between two events
            (Forward amortization)
            
            Arguments:
            origin_time -- original timestamp of an receive event
            new_time -- corrected/synchronized timestamp of the event
        """
        if new_time > origin_time and new_time > \
        (self._last_event_time + self._minimal_event_diff):
            self.time_offset += (new_time - max([origin_time, \
            self._last_event_time + self._minimal_event_diff]))
    
    def _backward_amortization(self, origin_time, new_time):
        """ Applies the backward amortization 
        
            Arguments:
            origin_time -- original timestamp of an receive event
            new_time -- corrected/synchronized timestamp of the event
        """
        offset = new_time - origin_time
        linear_send_events = copy.deepcopy(self._send_events)

        # Reduces collective messages into one
        for t in linear_send_events.keys():
            send_events = self._send_events[t]
            if len(send_events) > 1:
                index = send_events.index(min([e.offset for e in send_events]))
                linear_send_events[t] = send_events[index]
            else:
                linear_send_events[t] = linear_send_events[t][0]
        
        # Eliminates send events which break linear growth of the offsets
        delete_events = Queue()
        previous = SendEvent()
        for time, event in linear_send_events.iteritems():
            event.time = time
            if previous.offset >= event.offset or previous.offset >= offset:
                delete_events.put(previous.time)
            previous = event
        # Last event is not checked in the loop above, this checks it
        if previous.offset >= offset:
            delete_events.put(previous.time)
        length = delete_events.qsize()
        while length > 0:
            linear_send_events.pop(delete_events.get(), None)
            length -= 1
        
        # Repair times
        last_event = self._data_list.pop()
        send_event = [0]
        local_offset = offset
        # Is there any event that cannot be shifted by full amount of the offset
        if linear_send_events:
            send_event = linear_send_events.popitem(False)
            local_offset = send_event[1].offset
        new_send_events = OrderedDict()
        for index, event in enumerate(self._data_list):
            if event[0] == "M":
                tmp_time = event[1]
                time = tmp_time + local_offset
                event[1] = time
                new_send_events[time] = []
                for e in self._send_events[tmp_time]:
                    e.offset -= local_offset
                    new_send_events[time].append(e)
                self._last_refilled_send_time = time
                if tmp_time == send_event[0]:
                    if linear_send_events:
                        send_event = linear_send_events.popitem(False)
                        local_offset = send_event[1].offset
                    else:
                        send_event = [0]
                        local_offset = offset
            else:
                event[1] += local_offset
            if event[0] == "R":
                send_time = self._receive_send_table[index].get_sent_time()
                origin_id = self._receive_send_table[index].origin_id
                self._messenger.refill_received_time(origin_id, send_time, \
                                                                    event[1], \
                                                                    self.process_id, \
                                                                    False)
        self._send_events = new_send_events
        self._data_list.append(last_event)
        
    def _predict_backward_amortization(self):
        """ Returns True if the backward amortization should be done """
        if not self._backward_amort:
            return False
        if not self.get_next_event_name() == "Recv ":
            return False
        if self._last_event_time == 0:
            return False
        
        send_time = 0
        sender = self.get_msg_sender()
        try:
            send_time = self._messages[sender][self.process_id].get_and_keep()[1]
        except:
            print "Failed to load a send time from the shared variable messages.\
                 Sender: {0}, Receiver: {1}, Received time: {2}"\
                 .format(sender, self.process_id, self.get_next_event_time())
            return False
        
        origin_time = self.get_next_event_time()
        new_time = max([send_time + self._minimum_msg_delay, origin_time, \
                           self._last_event_time + \
                           self._minimal_event_diff])
        if new_time > origin_time and new_time > \
                (self._last_event_time + self._minimal_event_diff):
            return True
        else:
            return False
    
    def are_receive_times_refilled(self):
        """ Returns True if all current send events (SendEvent send_events) have 
        refilled the receive time field. THIS MUST BE CALLED BEFORE EACH 
        RECEIVE EVENT'S PROCESSING IF THE BACKWARD AMORTIZATION IS TURNED ON!"""
        if not self._predict_backward_amortization():
            self._is_backward_amortization = False
            return True
        times = self._send_events.keys()
        if self._last_refilled_send_time is not None:
            start = times.index(self._last_refilled_send_time)
            times = times[start:]
        for t in times:
            for e in self._send_events[t]:
                if e.receive == 0:
                    self._missing_receive_time_process_id = e.receiver
                    self._is_backward_amortization = False
                    return False
        self._is_backward_amortization = True
        return True
    
    def refill_received_time(self, sent_time, received_time, receiver, new_record=True):
        """ Backward amortization - adds receive time for a specific sent time 
            and compute maximum offset
            
            Arguments:
            sent_time -- time of a corresponding send event
            receive_time -- time of a receipt of the msg to be filled
            new_record -- if True you are adding missing received time otherwise
                            you are updating an existing received time
        """
        for event in self._send_events[sent_time]:
            if event.receiver == receiver:
                event.receive = received_time
                event.offset = received_time - \
                    self._minimum_msg_delay - sent_time
                if new_record:
                    self._last_refilled_send_time = sent_time
                break
    
    def export_data(self):
        """ Returns synchronized data in a raw binary form. """
        stream = StringIO()
        stream.write(self._header_info)
        for event in self._data_list:
            event[1] = self.struct_basic.pack(event[1])
            for data in event:
                stream.write(data)
        export = stream.getvalue()
        stream.close()
        return export
    
    
    def get_missing_receive_time_process_id(self):
        """ Returns id of a process whose time of a receive event was 
        missing during the are_receive_times_refilled() method"""
        return self._missing_receive_time_process_id
    
#     def set_init_time(self, increment):
#         """ Increase initial time of a process by the increment value
#         
#             Arguments:
#             increment -- an integer value which is added to the initial time        
#         """
#         origin = self.info["inittime"]
#         newtime = str(int(origin) + increment)
#         self.info["inittime"] = newtime
#         self._header_info = self._header_info.replace(origin, newtime)
        
    
    def get_msg_sender(self):
        """ Returns None or the id of a process, who is the sender of the received
            message, if the next event is receive event
        """
        if self.get_next_event_name() == "Recv ":
            tmp_pointer = self.pointer
            self.pointer += 1
            origin_id = self._read_struct_receive()[1]
            self.pointer = tmp_pointer
            return origin_id
        else:
            return None
        
    def get_last_received_sent_time(self):
        """ Returns last received (got from messages) sent time. """
        return self._last_received_sent_time
    
    def get_last_receive_event_time(self):
        """ Returns time of last synchronized receive event """
        return self._last_receive_event_time
    
    def _repair_time(self, time, start_pointer, end_pointer):
        """ Overwrites original time in tracelog's data string with the new one 
            
            Arguments:
            time -- a new time to be saved
            start_pointer -- points to the start of event's data
            end_pointer -- points to the end of event ('s data)
        """
        event = self._data_list[-1]
        event.append(time)
        start_pointer += self.struct_basic.size
        if end_pointer is False:
            end_pointer = self.pointer
        event.append( self.data[ start_pointer : end_pointer ] )


    def _extra_time(self, time, pointer, receive=False, origin_id=None):
        """ Calls functions for time synchronization
        
            Arguments:
            time -- time to be synchronized
            pointer -- points to the start of event's data
            receive -- mark True if you want to synchronize receive event
            origin_id -- if receive is True, specify id of the sender
        """
        if not receive:
            return self._clock_check(time, pointer)
        else:
            if origin_id is None:
                raise Exception("Origin_id for a receive event not entered!")
            send_event = self._messages[origin_id][self.process_id].get()
            sent_time = send_event[1]
            self._receive_send_table[ len(self._data_list) - 1 ] = RSTableElement(send_event, origin_id)
            ctime = self._clock_check(time, pointer, False, True, sent_time)
            print "Progress ID{0}: {1}%".format(self.process_id, float(self.pointer) / float(len(self.data)) * 100)
            self._last_received_sent_time = sent_time
            return ctime

    def _extra_event_send(self, time, target_id):
        """ Adds send event to the message queue and to trace's list of sends
        
            Arguments:
            time -- already synchronized time of the send event
            target_id -- message recipient
        """
        self._messages[self.process_id][target_id].put(self._data_list[-1])
        send_event = SendEvent()
        send_event.receiver = target_id
        if time not in self._send_events.keys():
            self._send_events[time] = [send_event]
        else:
            self._send_events[time].append(send_event)
    
    def _extra_event(self, event):
        """ Stores event symbol into trace's data """
        self._data_list.append([event])
    
    def _extra_value(self):
        """ Retrieves record of the last processed event """
        return self._data_list[-1]
    
    def _extra_tokens_add(self, pointer, extra, values):
        if values:
            extra.append(self.data[pointer:self.pointer])
            
            
    
class SQueue(Queue):
    """ Classic Queue with possibility of reading an element to be popped """
    
    def __init__(self):
        Queue.__init__(self)
    
    def get_and_keep(self):
        """ Returns an element to be popped but it does not pop it. """
        value = self.get()
        self.queue.appendleft(value)
        return value
    
class Messenger(object):
    """ Connector between SyncedTracLog and SyncedTrace """
    
    def __init__(self, target):
        self._target = target
        
    def refill_received_time(self, target, send_time, receive_time, receiver, \
                            new_record=True):
        """ Pairs a receive time to the corresponding sent time and computes 
            maximum offset. Works only if the backward amortization is turned
            on.
            
            Arguments:
            target -- ID of a process whose data should be refilled
            send_time -- time of a corresponding send event
            receive_time -- time of a receipt of the msg to be filled
            new_record -- if True you are adding missing receive time otherwise \
                            you are updating an existing receive time
        """
        self._target.refill_received_time(target, send_time, receive_time, \
                                        receiver, new_record)
        

class SendEvent(object):
    """ Send event structure.
    
        Attributes:
        time -- sent time
        receive -- received time
        receiver -- a recipient of the message
        offset -- difference between received and sent time 
    """
    def  __init__(self):
        self.time = 0
        self.receive = 0
        self.receiver = None
        self.offset = 0
        
class RSTableElement(object):
    """ Reference to a send event """
    
    def __init__(self, send_event, origin_id):
        self.send_event = send_event
        self.origin_id = origin_id
    
    def get_sent_time(self):
        """ Returns time of sent event """
        return self.send_event[1]
    