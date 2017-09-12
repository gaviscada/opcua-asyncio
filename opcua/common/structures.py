"""
Support for custom structures in client and server
We only support a subset of features but should be enough
for custom structures
"""

import os
import importlib
import re
# The next two imports are for generated code
from datetime import datetime
import uuid

from lxml import objectify


from opcua.ua.ua_binary import Primitives
from opcua import ua


def get_default_value(uatype):
    if uatype == "String":
        return "None" 
    elif uatype == "Guid":
        return "uuid.uuid4()" 
    elif uatype in ("ByteString", "CharArray", "Char"):
        return None 
    elif uatype == "Boolean":
        return "True"
    elif uatype == "DateTime":
        return "datetime.utcnow()"
    elif uatype in ("Int16", "Int32", "Int64", "UInt16", "UInt32", "UInt64", "Double", "Float", "Byte", "SByte"):
        return 0
    else:
        return "ua." + uatype + "()"


class Struct(object):
    def __init__(self, name):
        self.name = name
        self.fields = []
        self.code = ""
        self.typeid = None

    def get_code(self):
        if not self.fields:
            return """

class {}(object):
    pass

""".format(self.name)
        self._make_constructor()
        self._make_from_binary()
        self._make_to_binary()
        return self.code

    def _make_constructor(self):
        self.code = """


class {0}(object):
    '''
    {0} structure autogenerated from xml
    '''
    def __init__(self, data=None):
        if data is not None:
            self._binary_init(data)
            return
""".format(self.name)
        for field in self.fields:
            self.code += "        self.{} = {}\n".format(field.name, field.value)

    def _make_from_binary(self):
        self.code += '''
    @staticmethod
    def from_binary(data):
        return {}(data=data)

    def _binary_init(self, data):
'''.format(self.name)
        for field in self.fields:
            if hasattr(Primitives, field.uatype):
                if field.array:
                    self.code += '        self.{} = ua.ua_binary.Primitives.{}.unpack_array(data)\n'.format(field.name, field.uatype)
                else:
                    self.code += '        self.{} = ua.ua_binary.Primitives.{}.unpack(data)\n'.format(field.name, field.uatype)
            else:
                if field.array:
                    self.code += '''
        length = ua.ua_binary.Primitives.Int32.unpack(data)
        if length == -1:
            self.{0} = None
        else:
            self.{0} = [ua.{1}.from_binary(data) for _ in range(length)]
'''.format(field.name, field.uatype)
                else:
                    self.code += "        self.{} = ua.{}.from_binary(data)\n".format(field.name, field.uatype)

    def _make_to_binary(self):
        self.code += '''
    def to_binary(self):
        packet = []
'''
        for field in self.fields:
            if hasattr(Primitives, field.uatype):
                if field.array:
                    self.code += '        packet.append(ua.ua_binary.Primitives.{}.pack_array(self.{}))\n'.format(field.uatype, field.name)
                else:
                    self.code += '        packet.append(ua.ua_binary.Primitives.{}.pack(self.{}))\n'.format(field.uatype, field.name)
            else:
                if field.array:
                    self.code += '''
        if self.{0} is None:
            packet.append(ua.ua_binary.Primitives.Int32.pack(-1))
        else:
            packet.append(ua.ua_binary.Primitives.Int32.pack(len(self.{0})))
            for element in self.{0}:
                packet.append(element.to_binary())
'''.format(field.name)
                else:
                    self.code += "        packet.append(self.{}.to_binary())\n".format(field.name)
        self.code += '        return b"".join(packet)'


class Field(object):
    def __init__(self, name):
        self.name = name
        self.uatype = None
        self.value = None
        self.array = False


class StructGenerator(object):
    def __init__(self):
        self.model = []

    def make_model_from_string(self, xml):
        obj = objectify.fromstring(xml)
        self._make_model(obj)

    def make_model_from_file(self, path):
        obj = objectify.parse(path)
        root = obj.getroot()
        self._make_model(root)

    def _make_model(self, root):
        for child in root.iter("{*}StructuredType"):
            struct = Struct(child.get("Name"))
            array = False
            for xmlfield in child.iter("{*}Field"):
                name = xmlfield.get("Name")
                if name.startswith("NoOf"):
                    array = True
                    continue
                field = Field(_clean_name(name))
                field.uatype = xmlfield.get("TypeName")
                if ":" in field.uatype:
                    field.uatype = field.uatype.split(":")[1]
                field.uatype = _clean_name(field.uatype)
                field.value = get_default_value(field.uatype)
                if array:
                    field.array = True
                    field.value = []
                    array = False
                struct.fields.append(field)
            self.model.append(struct)

    def save_to_file(self, path, register=False):
        _file = open(path, "wt")
        self._make_header(_file)
        for struct in self.model:
            _file.write(struct.get_code())
        if register:
            _file.write(self._make_registration())
        _file.close()

    def _make_registration(self):
        code = "\n\n"
        for struct in self.model:
            code += "ua.register_extension_object('{name}', ua.NodeId.from_string('{nodeid}'), {name})\n".format(name=struct.name, nodeid=struct.typeid)
        return code

    def get_python_classes(self, env=None):
        """
        generate Python code and execute in a new environment
        return a dict of structures {name: class}
        Rmw: Since the code is generated on the fly, in case of error the stack trace is 
        not available and debugging is very hard...
        """
        if env is None:
            env = {}
        #  Add the required libraries to dict
        if "ua" not in env:
            env['ua'] = ua
        if "datetime" not in env:
            env['datetime'] = datetime
        if "uuid" not in env:
            env['uuid'] = uuid
        # generate classes one by one and add them to dict
        for struct in self.model:
            code = struct.get_code()
            exec(code, env)
        return env

    def save_and_import(self, path, append_to=None):
        """
        save the new structures to a python file which be used later
        import the result and return resulting classes in a dict
        if append_to is a dict, the classes are added to the dict
        """
        self.save_to_file(path)
        name = os.path.basename(path)
        name = os.path.splitext(name)[0]
        mymodule = importlib.import_module(name)
        if append_to is None:
            result = {}
        else:
            result = append_to
        for struct in self.model:
            result[struct.name] = getattr(mymodule, struct.name)
        return result

    def _make_header(self, _file):
        _file.write("""
'''
THIS FILE IS AUTOGENERATED, DO NOT EDIT!!!
'''

from datetime import datetime
import uuid

from opcua import ua
""")

    def set_typeid(self, name, typeid):
        for struct in self.model:
            if struct.name == name:
                struct.typeid = typeid
                return


def load_type_definitions(server, nodes=None):
    """
    Download xml from given variable node defining custom structures.
    If no node is given, attemps to import variables from all nodes under
    "0:OPC Binary"
    the code is generated and imported on the fly. If you know the structures
    are not going to be modified it might be interresting to copy the generated files
    and include them in you code
    """
    if nodes is None:
        nodes = []
        for desc in server.nodes.opc_binary.get_children_descriptions():
            if desc.BrowseName != ua.QualifiedName("Opc.Ua"):
                nodes.append(server.get_node(desc.NodeId))
    
    structs_dict = {}
    generators = []
    for node in nodes:
        xml = node.get_value()
        xml = xml.decode("utf-8")
        generator = StructGenerator()
        generators.append(generator)
        generator.make_model_from_string(xml)
        # generate and execute new code on the fly
        generator.get_python_classes(structs_dict)
        # same but using a file that is imported. This can be usefull for debugging library
        #name = node.get_browse_name().Name
        # Make sure structure names do not contain charaters that cannot be used in Python class file names
        #name = _clean_name(name)
        #name = "structures_" + node.get_browse_name().Name
        #generator.save_and_import(name + ".py", append_to=structs_dict)

        # register classes
        # every children of our node should represent a class
        for ndesc in node.get_children_descriptions():
            ndesc_node = server.get_node(ndesc.NodeId)
            ref_desc_list = ndesc_node.get_references(refs=ua.ObjectIds.HasDescription, direction=ua.BrowseDirection.Inverse)
            if ref_desc_list:  #some server put extra things here
                name = _clean_name(ndesc.BrowseName.Name)
                if not name in structs_dict:
                    print("Error {} is found as child of binary definition node but is not found in xml".format(name))
                    continue
                nodeid = ref_desc_list[0].NodeId
                ua.register_extension_object(name, nodeid, structs_dict[name])
                # save the typeid if user want to create static file for type definitnion
                generator.set_typeid(name, nodeid.to_string())
    return generators, structs_dict


def _clean_name(name):
    """
    Remove characters that might be present in  OPC UA structures
    but cannot be part of of Python class names
    """
    name = re.sub(r'\W+', '_', name)
    name = re.sub(r'^[0-9]+', r'_\g<0>', name)

    return name
