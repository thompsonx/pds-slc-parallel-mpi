#
#    Copyright (C) 2016 Tomas Panoc
#

import copy 
from tracelog import Trace
from Queue import Queue
from collections import OrderedDict
from cStringIO import StringIO

MAIN_COMMUNICATION = 1
BA_COMMUNICATION = 2
      
class ParallelSyncedTrace(Trace):
    
    def __init__(self, data, process_id, pointer_size, minimal_event_diff, \
                                     minimum_msg_delay, \
                                     forward_amort, \
                                     backward_amort, \
                                     communicator):
        """ Synchronizes events of one process.
        
            Arguments:
            data -- content of a process's *.ktt file
            process_id -- ID of the process
            pointer_size -- 4 or 8, type of binary data within the *.ktt file
            minimal_event_diff -- see the SyncedTraceLog class
            minimum_msg_delay -- see the SyncedTraceLog class
            forward_amort -- see the SyncedTraceLog class
            backward_amort -- see the SyncedTraceLog class
            communicator -- MPI communicator
        """
        Trace.__init__(self, data, process_id, pointer_size)
        self._messages = None
        self._minimal_event_diff = minimal_event_diff
        self._minimum_msg_delay = minimum_msg_delay
        self._forward_amort = forward_amort
        self._backward_amort = backward_amort
        self._communicator = communicator
        self._data_list = []
        self._header_info = self.data[:self.pointer]
        self._last_event_time = 0
        self._send_events = OrderedDict()
        self._last_received_sent_time = 0
        self._last_receive_event_time = 0
        self._violating_recv_events = OrderedDict()
        self._requests = []
        
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
        if self._backward_amort:
            if newtime > time:
                self._violating_recv_events[newtime] = newtime - time
                self._last_violating_recv_index = len(self._data_list) - 1
        
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
        if new_time > origin_time:
            self.time_offset += new_time - origin_time
    
    def do_backward_amortization(self):
        """ Applies the backward amortization 
        """

        for r in self._requests:
            time, request, target = r
            received_time = request.wait()
            self.refill_received_time(time, received_time, target)

        if not self._violating_recv_events.keys():
            return
        
        # Reduces collective messages into one
        for t in self._send_events.keys():
            send_events = self._send_events[t]
            if len(send_events) > 1:
                index = send_events.index(min([e.offset for e in send_events]))
                self._send_events[t] = send_events[index]
            else:
                self._send_events[t] = send_events[0]
        
        offset = self._violating_recv_events[self._violating_recv_events.keys()[-1]]
        for event in self._data_list[ self._last_violating_recv_index - 1 : : -1 ]:
            if event[0] == "M":
                max_offset = self._send_events[ event[1] ].offset
                if max_offset < offset:
                    offset = max_offset
            tmp_time = event[1]
            event[1] += offset
            if event[0] == "R" and tmp_time in self._violating_recv_events.keys():
                offset += self._violating_recv_events[ tmp_time ]
    
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
                break
        
    
    def export_data(self, path):
        """ Returns synchronized data in a raw binary form. """
        
        stream = StringIO()
        stream.write(self._header_info)
        for event in self._data_list:
            event[1] = self.struct_basic.pack(event[1])
            for data in event:
                stream.write(data)
        export = stream.getvalue()
        stream.close()
        with open(path, "wb") as f:
            f.write(export)
        
    
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
            req = self._communicator.irecv(source=origin_id, tag=MAIN_COMMUNICATION)
            sent_time = req.wait()
            ctime = self._clock_check(time, pointer, False, True, sent_time)
            if self._backward_amort:
                self._communicator.isend(ctime, dest=origin_id, tag=BA_COMMUNICATION)
            self._last_received_sent_time = sent_time
            return ctime

    def _extra_event_send(self, time, target_id):
        """ Adds trace's list of sends
        
            Arguments:
            time -- already synchronized time of the send event
            target_id -- message recipient
        """
        self._communicator.isend(time, dest=target_id, tag=MAIN_COMMUNICATION)
        if self._backward_amort:
            self._requests.append((time, self._communicator.irecv(source=target_id, 
                                                        tag=BA_COMMUNICATION),
                                target_id))

        send_event = SendEvent()
        send_event.receiver = target_id
        if time not in self._send_events.keys():
            self._send_events[time] = [send_event]
        else:
            self._send_events[time].append(send_event)

        if self._backward_amort:
            del_req = Queue()
            for r in self._requests:
                packet = r[1].test()
                if packet[0]:
                    time, request, target = r
                    received_time = packet[1]
                    self.refill_received_time(time, received_time, target)
                    del_req.put(r)
                else:
                    break
            while not del_req.empty():
                r = del_req.get()
                self._requests.remove(r)
    
    def _extra_event(self, event):
        """ Stores event symbol into trace's data """
        self._data_list.append([event])
    
    def _extra_value(self):
        """ Retrieves record of the last processed event """
        return self._data_list[-1]
    
    def _extra_tokens_add(self, pointer, extra, values):
        if values:
            extra.append(self.data[pointer:self.pointer])
            
        

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
        
    