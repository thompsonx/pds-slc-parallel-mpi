#
#    Copyright (C) 2012-2016 Stanislav Bohm,
#                            Martin Surkovsky,
#                            Tomas Panoc
#
#    THIS FILE IS MODIFIED VARIANT OF ORIGINAL TRACELOG.PY FROM KAIRA.
#    IT WAS MODIFIED FOR EDUCATIONAL PURPOSES AND YOU MAY USE AND SHARE IT
#    FOR FREE.
#
#    Kaira is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, version 3 of the License, or
#    (at your option) any later version.
#
#    Kaira is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Kaira.  If not, see <http://www.gnu.org/licenses/>.
#


import xml.etree.ElementTree as xml
import struct
import os

zero_char = chr(0)

def read_header(filename):
        with open(filename, "r") as f:
            header = xml.fromstring(f.readline())
            pointer_size = xml_int(header, "pointer-size")
        return pointer_size

def read_trace(filename, process_id):
    file_name = "{0}-{1}-0.ktt".format(
        filename,
        process_id)
    with open(file_name, "rb") as f:
        return (f.read(), file_name)
        
def xml_int(element, attr, default = None):
    if element.get(attr) is None:
        if default is not None:
            return default
        else:
            raise Exception("Element has no attribute: " + attr)
    return int(float(element.get(attr)))

def trim_filename_suffix(filename):
    return os.path.splitext(filename)[0]

class Trace:

    struct_basic = struct.Struct("<Q")
    struct_transition_fired = struct.Struct("<Qi")
    struct_spawn = struct.Struct("<Qi")
    struct_send = struct.Struct("<QQii")
    struct_receive = struct.Struct("<Qi")

    struct_token_4 = struct.Struct("<Li")
    struct_token_8 = struct.Struct("<Qi")

    struct_int = struct.Struct("<i")
    struct_double = struct.Struct("<d")

    def __init__(self, data, process_id, pointer_size):
        self.data = data
        self.pointer = 0
        self.process_id = process_id
        self.time_offset = 0
        if pointer_size == 4:
            self.struct_token = self.struct_token_4
        elif pointer_size == 8:
            self.struct_token = self.struct_token_8
        else:
            Exception("Invalid pointer size")
        self.info = self._read_header()

    def get_init_time(self):
        s = self.info.get("inittime")
        if s is not None:
            return int(s)
        else:
            return 0

    def is_next_event_visible(self):
        t = self.data[self.pointer]
        return t != "I" and t != "M" and t != "N"

    def get_next_event_name(self):
        """ Return name of event as 5-character string """
        t = self.data[self.pointer]
        if t == "T":
            return "Fire "
        elif t == "F":
            return "Fin  "
        elif t == "M":
            return "Send "
        elif t == "N":
            return "MSend"
        elif t == "R":
            return "Recv "
        elif t == "S":
            return "Spawn"
        elif t == "I":
            return "Idle "
        elif t == "H" or t == "Q": # "H" for backward compatability
            return "Quit "

    def process_event(self, runinstance=None):
        t = self.data[self.pointer]
        self.pointer += 1
        self._extra_event(t)
        if runinstance is not None:
            runinstance.pre_event()
        if t == "T":
            self._process_event_transition_fired(runinstance)
        elif t == "F":
            self._process_event_transition_finished(runinstance)
        elif t == "R":
            return self._process_event_receive(runinstance)
        elif t == "S":
            return self._process_event_spawn(runinstance)
        elif t == "I":
            return self._process_event_idle(runinstance)
        elif t == "Q":
            # This is called only when transition that call ctx.quit is not traced
            self.pointer -= 1 # _process_event_quit expect the pointer at "Q"
            self._process_event_quit(runinstance)
        else:
            raise Exception("Invalid event type '{0}/{1}' (pointer={2}, process={3})"
                                .format(t, ord(t), hex(self.pointer), self.process_id))

    def is_pointer_at_end(self):
        return self.pointer >= len(self.data)

    def process_tokens_add(self, runinstance, send_time=0):
        place_id = None
        token_pointer = None
        values = []
        pointer1 = self.pointer
        extra = self._extra_value()
        while not self.is_pointer_at_end():
            t = self.data[self.pointer]
            if t == "t":
                if  runinstance is not None and place_id is not None:
                    runinstance.add_token(place_id, token_pointer, values, send_time)
                values = []
                self.pointer += 1
                token_pointer, place_id = self._read_struct_token()
            elif t == "i":
                self.pointer += 1
                value = self._read_struct_int()
                values.append(value)
            elif t == "d":
                self.pointer += 1
                value = self._read_struct_double()
                values.append(value)
            elif t == "s":
                self.pointer += 1
                value = self._read_cstring()
                values.append(value)
            elif t == "M":
                self.pointer += 1
                self._process_event_send(runinstance)
            else:
                if runinstance is not None and place_id is not None and token_pointer is not None:
                    runinstance.add_token(place_id, token_pointer, values, send_time)
                break

        if runinstance is not None and self.is_pointer_at_end() and place_id is not None:
            runinstance.add_token(place_id, token_pointer, values, send_time)
        
        self._extra_tokens_add(pointer1, extra, values)
        
        
    def process_tokens_remove(self, runinstance):
        while not self.is_pointer_at_end():
            t = self.data[self.pointer]
            if t == "r":
                self.pointer += 1
                token_pointer, place_id = self._read_struct_token()
                if runinstance is not None:
                    runinstance.remove_token(place_id, token_pointer)
            elif runinstance is not None and t == "M":
                self.pointer += 1
                self._process_event_send(runinstance)
            else:
                break

    def get_next_event_time(self):
        if self.is_pointer_at_end():
            return None
        else:
            return self.struct_basic.unpack_from(self.data, self.pointer + 1)[0] + \
                   self.time_offset

    def _read_header(self):
        info = {}
        while True:
            key = self._read_cstring()
            value = self._read_cstring()
            if key == "" and value == "":
                break
            info[key] = value

        if "KairaThreadTrace" not in info or info["KairaThreadTrace"] != "1":
            raise Exception("Invalid format or version of KairaThreadTrace")
        return info

    def _read_struct_transition_fired(self):
        values = self.struct_transition_fired.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_transition_fired.size
        return values

    def _read_struct_transition_finished(self):
        values = self.struct_basic.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_basic.size
        return values

    def _read_struct_receive(self):
        values = self.struct_receive.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_receive.size
        return values

    def _read_struct_send(self):
        time, size, edge_id, count = self.struct_send.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_send.size
        values = [ self._read_struct_int() for i in xrange(count) ]
        return (time, size, edge_id, values)

    def _read_struct_spawn(self):
        values = self.struct_spawn.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_spawn.size
        return values

    def _read_struct_quit(self):
        values = self.struct_basic.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_basic.size
        return values

    def _process_end(self, runinstance):
        t = self.data[self.pointer]
        if t != "X":
            return
        self._extra_event(t)
        self.pointer += 1
        pointer1 = self.pointer
        values = self.struct_basic.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_basic.size
        self._extra_time(values[0], pointer1)
        if runinstance is not None:
            runinstance.event_end(self.process_id, values[0] + self.time_offset)

    def _process_event_transition_fired(self, runinstance):
        ptr = self.pointer
        time, transition_id = self._read_struct_transition_fired()
        pointer1 = self.pointer
        values = self._read_transition_trace_function_data()
        pointer2 = self.pointer
        self._extra_time(time, ptr)
        
        if runinstance is not None:
            self.pointer = pointer1
            runinstance.transition_fired(self.process_id,
                                         time + self.time_offset,
                                         transition_id,
                                         values)
            self.process_tokens_remove(runinstance)
            self.pointer = pointer2
            
        self._process_event_quit(runinstance)
        self.process_tokens_add(runinstance)
        self._process_end(runinstance)

    def _process_event_transition_finished(self, runinstance):
        pointer1 = self.pointer
        time = self._read_struct_transition_finished()[0]
        self._extra_time(time, pointer1)
        if runinstance is not None:
            runinstance.transition_finished(self.process_id,
                                            time + self.time_offset)
        self._process_event_quit(runinstance)
        self.process_tokens_add(runinstance)
        self._process_end(runinstance)

    def _process_event_send(self, runinstance):
        self._extra_event("M")
        pointer1 = self.pointer
        time, size, edge_id, target_ids = self._read_struct_send()
        extra = self._extra_time(time, pointer1)
        for target_id in target_ids:
            self._extra_event_send(extra, target_id)
            if runinstance is not None:
                runinstance.event_send(self.process_id,
                                       time + self.time_offset,
                                       target_id,
                                       size,
                                       edge_id)

    def _process_event_spawn(self, runinstance):
        pointer1 = self.pointer
        time, net_id = self._read_struct_spawn()
        self._extra_time(time, pointer1)
        if runinstance is not None:
            runinstance.event_spawn(self.process_id,
                                    time + self.time_offset,
                                    net_id)
        self.process_tokens_add(runinstance)

    def _process_event_quit(self, runinstance):
        t = self.data[self.pointer]
        if t != "Q":
            return
        self._extra_event(t)
        self.pointer += 1
        pointer1 = self.pointer
        time = self._read_struct_quit()[0]
        self._extra_time(time, pointer1)
        if runinstance is not None:
            runinstance.event_quit(self.process_id,
                                   time + self.time_offset)

    def _process_event_receive(self, runinstance):
        pointer1 = self.pointer
        time, origin_id = self._read_struct_receive()
        send_time = 0
        self._extra_time(time, pointer1, True, origin_id)
        if runinstance is not None:
            send_time = runinstance.event_receive(
                            self.process_id,
                            time + self.time_offset,
                            origin_id
                        ) or 1
        

        self.process_tokens_add(runinstance, send_time)
        self._process_end(runinstance)

    def _process_event_idle(self, runinstance):
        pointer1 = self.pointer
        time = self._read_struct_quit()[0]
        self._extra_time(time, pointer1)
        if runinstance is not None:
            runinstance.event_idle(self.process_id,
                                   time + self.time_offset)

    def _read_struct_token(self):
        values = self.struct_token.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_token.size
        return values

    def _read_struct_int(self):
        value = self.struct_int.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_int.size
        return value[0]

    def _read_struct_double(self):
        value = self.struct_double.unpack_from(self.data, self.pointer)
        self.pointer += self.struct_double.size
        return value[0]

    def _read_cstring(self):
        start = self.pointer
        while self.data[self.pointer] != zero_char:
            self.pointer += 1
        s = self.data[start:self.pointer]
        self.pointer += 1
        return s

    def _read_transition_trace_function_data(self):
        values = []
        while not self.is_pointer_at_end():
            t = self.data[self.pointer]
            if t == "r":
                self.pointer += 1
                self._read_struct_token()
            elif t == "i":
                self.pointer += 1
                value = self._read_struct_int()
                values.append(value)
            elif t == "d":
                self.pointer += 1
                value = self._read_struct_double()
                values.append(value)
            elif t == "s":
                self.pointer += 1
                value = self._read_cstring()
                values.append(value)
            else:
                break
        return values

    def _extra_event(self, event):
        """ Reserved for extending the behavior in child classes (SyncedTrace)"""
        pass
    def _extra_time(self, time, pointer, receive=False, origin_id=None):
        """ Reserved for extending the behavior in child classes (SyncedTrace)"""
        return None
    def _extra_event_send(self, time, target_id):
        """ Reserved for extending the behavior in child classes (SyncedTrace)"""
        pass
    def _extra_tokens_add(self, pointer, extra, values):
        """ Reserved for extending the behavior in child classes (SyncedTrace)"""
        pass
    def _extra_value(self):
        """ Reserved for extending the behavior in child classes (SyncedTrace)"""
        return None