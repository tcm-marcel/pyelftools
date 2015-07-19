#-------------------------------------------------------------------------------
# elftools: dwarf/die.py
#
# DWARF Debugging Information Entry
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
from collections import namedtuple, OrderedDict
import os

from ..common.exceptions import DWARFError
from ..common.py3compat import bytes2str, iteritems
from ..common.utils import struct_parse, preserve_stream_pos
from .enums import DW_FORM_raw2name


# AttributeValue - describes an attribute value in the DIE:
#
# name:
#   The name (DW_AT_*) of this attribute
#
# form:
#   The DW_FORM_* name of this attribute
#
# value:
#   The value parsed from the section and translated accordingly to the form
#   (e.g. for a DW_FORM_strp it's the actual string taken from the string table)
#
# raw_value:
#   Raw value as parsed from the section - used for debugging and presentation
#   (e.g. for a DW_FORM_strp it's the raw string offset into the table)
#
# offset:
#   Offset of this attribute's value in the stream (absolute offset, relative
#   the beginning of the whole stream)
#

class AttributeValue(object):
    def __init__(self, name, form, raw_value, offset, die):
        #TODO
        """ TODO
        """
        self.name = name
        self.form = form
        self.raw_value = raw_value
        self.value = None
        self.offset = offset
        self.die = die
        
        self._translate_value()
            
    def _translate_value(self):
        #TODO
        
        if self.value is None:
            self.value = self._translate_attr_value(self.form, self.raw_value)
    
    def _translate_attr_value(self, form, raw_value):
        """ Translate a raw value according to the form
            (not complete!)
        """
        value = None
        if form == 'DW_FORM_strp':
            with preserve_stream_pos(self.die.stream):
                value = self.die.dwarfinfo.get_string_from_table(raw_value)
        elif form == 'DW_FORM_flag':
            value = not raw_value == 0
        elif form == 'DW_FORM_indirect':
            try:
                form = DW_FORM_raw2name[raw_value]
            except KeyError as err:
                raise DWARFError(
                        'Found DW_FORM_indirect with unknown raw_value=' +
                        str(raw_value))

            raw_value = struct_parse(
                self.die.cu.structs.Dwarf_dw_form[form], self.die.stream)
            # Let's hope this doesn't get too deep :-)
            value = self._translate_attr_value(form, raw_value)
        elif form == 'DW_FORM_ref4':
            value = self.die.cu.get_DIE_at_offset(raw_value)
        
        return value
    
    def __repr__(self):
        if isinstance(self.value, DIE):
            s = 'form=%-18s, value=%-10s, raw_value=%s' % (
            self.form, self.value.tag, self.raw_value)
        else:
            s = 'form=%-18s, value=%-10s, raw_value=%s' % (
            self.form, self.value, self.raw_value)
        return s

    def __str__(self):
        return self.__repr__()


class DIE(object):
    """ A DWARF debugging information entry. On creation, parses itself from
        the stream. Each DIE is held by a CU.

        Accessible attributes:

            tag:
                The DIE tag

            size:
                The size this DIE occupies in the section

            offset:
                The offset of this DIE in the stream

            attributes:
                An ordered dictionary mapping attribute names to values. It's
                ordered to preserve the order of attributes in the section

            has_children:
                Specifies whether this DIE has children

            abbrev_code:
                The abbreviation code pointing to an abbreviation entry (note
                that this is for informational pusposes only - this object
                interacts with its abbreviation table transparently).

        See also the public methods.
    """
    def __init__(self, cu, stream, offset):
        """ cu:
                CompileUnit object this DIE belongs to. Used to obtain context
                information (structs, abbrev table, etc.)

            stream, offset:
                The stream and offset into it where this DIE's data is located
        """
        self.cu = cu
        self.dwarfinfo = self.cu.dwarfinfo # get DWARFInfo context
        self.stream = stream
        self.offset = offset

        self.attributes = OrderedDict()
        self.tag = None
        self.has_children = None
        self.abbrev_code = None
        self.size = 0
        self._children = []
        self._parent = None
        
        self._parse_DIE()

    def is_null(self):
        """ Is this a null entry?
        """
        return self.tag is None

    def get_parent(self):
        """ The parent DIE of this DIE. None if the DIE has no parent (i.e. a
            top-level DIE).
        """
        return self._parent

    def get_full_path(self):
        """ Return the full path filename for the DIE.

            The filename is the join of 'DW_AT_comp_dir' and 'DW_AT_name',
            either of which may be missing in practice. Note that its value is
            usually a string taken from the .debug_string section and the
            returned value will be a string.
        """
        comp_dir_attr = self.attributes.get('DW_AT_comp_dir', None)
        comp_dir = bytes2str(comp_dir_attr.value) if comp_dir_attr else ''
        fname_attr = self.attributes.get('DW_AT_name', None)
        fname = bytes2str(fname_attr.value) if fname_attr else ''
        return os.path.join(comp_dir, fname)

    def iter_children(self):
        """ Yield all children of this DIE
        """
        return iter(self._children)

    def iter_siblings(self):
        """ Yield all siblings of this DIE
        """
        if self._parent:
            for sibling in self._parent.iter_children():
                if sibling is not self:
                    yield sibling
        else:
            raise StopIteration()

    # The following methods are used while creating the DIE and should not be
    # interesting to consumers
    #
    def add_child(self, die):
        self._children.append(die)

    def set_parent(self, die):
        self._parent = die

    #------ PRIVATE ------#

    def __repr__(self):
        s = 'DIE %s, size=%s, has_chidren=%s\n' % (
            self.tag, self.size, self.has_children)
        for attrname, attrval in iteritems(self.attributes):
            s += '    |%-18s:  %s\n' % (attrname, attrval)
        return s

    def __str__(self):
        return self.__repr__()

    def _parse_DIE(self):
        """ Parses the DIE info from the section, based on the abbreviation
            table of the CU
        """
        structs = self.cu.structs

        # A DIE begins with the abbreviation code. Read it and use it to
        # obtain the abbrev declaration for this DIE.
        # Note: here and elsewhere, preserve_stream_pos is used on operations
        # that manipulate the stream by reading data from it.
        #
        self.abbrev_code = struct_parse(
            structs.Dwarf_uleb128(''), self.stream, self.offset)

        # This may be a null entry
        if self.abbrev_code == 0:
            self.size = self.stream.tell() - self.offset
            return

        with preserve_stream_pos(self.stream):
            abbrev_decl = self.cu.get_abbrev_table().get_abbrev(
                self.abbrev_code)
        self.tag = abbrev_decl['tag']
        self.has_children = abbrev_decl.has_children()
        
        for name, form in abbrev_decl.iter_attr_specs():
            attr_offset = self.stream.tell()
            raw_value = struct_parse(structs.Dwarf_dw_form[form], self.stream)
            
            self.attributes[name] = AttributeValue(
                name=name,
                form=form,
                raw_value=raw_value,
                offset=attr_offset,
                die=self
            )
        
        self.size = self.stream.tell() - self.offset
    
    def _translate_attr_values(self):
        """ Translate all values of the attributes
        """
        for name, attr in self.attributes.items():
            attr._translate_value()
