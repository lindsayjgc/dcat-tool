"""
Validate data inventory records.
"""

import os
import os.path
from os.path import abspath, dirname
import sys
import pprint
import glob
import time
import rdflib
from rdflib import Dataset, Graph, URIRef, Literal, Namespace

# New imports
from rdflib import Dataset, Graph, URIRef, Literal, Namespace, BNode
from rdflib.namespace import DC, XSD, RDF, NamespaceManager
import json
from dateutil.parser import parse
from pyshacl import validate
from datetime import datetime

import template_reader
import easy_workbook

DHS           = Namespace("http://github.com/usdhs/dcat-tool/0.1")
XSD           = Namespace("http://www.w3.org/2001/XMLSchema#")
RDFS          = Namespace("http://www.w3.org/2000/01/rdf-schema#")

SCHEMATA_DIR  = os.path.join(dirname(abspath( __file__ )) , "../schemata")
COLLECT_TTL   = os.path.join(SCHEMATA_DIR, "dhs_collect.ttl")
DEFAULT_WIDTH = 15              # Excel spreadsheet default width
DEFAULT_TYPE  = XSD.string

"""
CI_QUERY is the query to create the collection instrument
It finds all of the properties that are within the dhs:dataInventoryRecord and
then does a join with OPTIONAL on several other objects we would like to extract.on several

?aShapeName is the name of the blank nodes that are actually the column constraints in the schema.
?aTitle is the title in the excel spreadsheet
?aGroup is the group within the excel spreadsheet
"""

CI_QUERY = """
SELECT DISTINCT ?aProperty ?aTitle ?aPropertyComment ?aShapeComment ?aType ?aWidth ?aGroup ?aPropertyDefinedBy ?aPropertyLabel ?aMinCount ?aDataType
WHERE {
{
  dhs:dataInventoryRecordShape sh:property ?aShapeName .
  ?aShapeName sh:path ?aProperty .

  OPTIONAL { ?aProperty  rdfs:range      ?aType . }
  OPTIONAL { ?aProperty  rdfs:comment    ?aPropertyComment . }
  OPTIONAL { ?aProperty  rdfs:isDefinedBy    ?aPropertyDefinedBy . }
  OPTIONAL { ?aProperty  rdfs:label    ?aPropertyLabel . }
  OPTIONAL { ?aShapeName dhs:excelWidth  ?aWidth . }
  OPTIONAL { ?aShapeName dt:title        ?aTitle . }
  OPTIONAL { ?aShapeName dt:group        ?aGroup . }
  OPTIONAL { ?aShapeName rdfs:comment    ?aShapeComment . }
  OPTIONAL { ?aShapeName sh:minCount     ?aMinCount . }
  OPTIONAL { ?aShapeName sh:datatype     ?aDataType . }
  FILTER (!BOUND(?aPropertyLabel) || lang(?aPropertyLabel) = "" || lang(?aPropertyLabel) = "en" || lang(?aPropertyLabel) = "en-US")
  } 
  UNION 
  {
  dhs:characteristicsShape sh:property ?aShapeName .
  ?aShapeName sh:path ?aProperty .

  OPTIONAL { ?aProperty  rdfs:range      ?aType . }
  OPTIONAL { ?aProperty  rdfs:comment    ?aPropertyComment . }
  OPTIONAL { ?aProperty  rdfs:isDefinedBy    ?aPropertyDefinedBy . }
  OPTIONAL { ?aProperty  rdfs:label    ?aPropertyLabel . }
  OPTIONAL { ?aShapeName dhs:excelWidth  ?aWidth . }
  OPTIONAL { ?aShapeName dt:title        ?aTitle . }
  OPTIONAL { ?aShapeName dt:group        ?aGroup . }
  OPTIONAL { ?aShapeName rdfs:comment    ?aShapeComment . }
  OPTIONAL { ?aShapeName sh:minCount     ?aMinCount . }
  OPTIONAL { ?aShapeName sh:datatype     ?aDataType . }
  FILTER (!BOUND(?aPropertyLabel) || lang(?aPropertyLabel) = "" || lang(?aPropertyLabel) = "en" || lang(?aPropertyLabel) = "en-US")
  }
}
"""

def dhs_collect_graph(schema_file = COLLECT_TTL):
    g    = Graph()
    g.parse(schema_file, format='turtle')
    return g

def dcatv3_ontology(schemata_dir = SCHEMATA_DIR, schema_file = COLLECT_TTL):
    """Returns a graph of the DHS ontology for the data inventory program"""
    g    = Graph()
    seen = set()
    for fname in glob.glob( os.path.join(schemata_dir,"*.ttl")) + [schema_file]:
        if fname and fname not in seen:
            fname = os.path.abspath(fname)
            g.parse(fname, format='turtle')
            seen.add(fname)
    if not seen:
        raise RuntimeError("No schema files specified")
    return g

class Simplifier:
    def __init__(self, graph):
        self.graph = graph
    def simplify(self, token, namespace=True):
        for prefix,ns in self.graph.namespaces():
            if ns:
                if token.startswith(ns):
                    if namespace:
                        return prefix+":"+token[len(ns):]
                    else:
                        return token[len(ns):]
        return token

def should_skip(d):
    """Skip query responses that are not in English"""
    # Skip property comments that are not in english
    try:
        if d['aPropertyComment'].language not in ['en', None, '']:
            return True
    except (KeyError,AttributeError) as e:
        pass
    return False

def label_lang_check(labelIn):
    try:
        if labelIn.language in ['en']:
            return True
    except (KeyError,AttributeError) as e:
        pass
    return False

class ValidationFail( Exception ):
    pass

class Validator:
    def __init__(self, schemata_dir = SCHEMATA_DIR, schema_file = COLLECT_TTL, debug=False):
        self.debug = debug
        self.g = dcatv3_ontology(schemata_dir, schema_file)
        self.get_template_column_info_objs()
        self.seenIDs = set()
        self.rows    = []

    def clear(self):
        """Clear the seenIDs"""
        self.seenIDs.clear()

    def cleanGraph(self):
        """Return a graph with the namespace but none of the tripples"""
        g2 = Graph()
        # Copy over the namespaces from the triples we read to the graph we are producing
        for ns_prefix,namespace in self.g.namespaces():
            g2.bind(ns_prefix, namespace)
            #print("adding namespace prefix",ns_prefix,namespace)
        return g2

    def augmentGraph(self, g2, queryResult):
        # Now create the collection graph
        try:
            g2.add( (queryResult['aProperty'], RDFS.range,   queryResult['aType']) )
        except KeyError as e:
            pass

        try:
            g2.add( (queryResult['aProperty'], RDFS.comment,   queryResult['aComment']) )
        except KeyError as e:
            pass

    def get_query_dict(self):
        # creates a sorted list to output everything in the expected order
        baseDict = self.g.query( CI_QUERY )
        sortedDict = []
        groupList = []
        for q in baseDict:
            if q['aGroup'] not in groupList:
                groupList.append(q['aGroup'])
                #print('test it: ' + str(q['aGroup']))
        for k in groupList:
            for q in baseDict:
                if q['aGroup'] == k:
                    sortedDict.append(q)

        for r in sortedDict:
            d = r.asdict()
            if self.debug:
                print(d)
            if should_skip(d):
                if self.debug:
                    print(">> skip",d)
                continue
            yield d

    def get_descriptions(self):
        """Returns an iterator of tuples in the form (group, simplifed_property, description)"""
        simp = Simplifier(self.g)
        counter = 0
        for d in self.get_query_dict():
            group = d.get('aGroup', '')
            if(group == ''):
                continue
            comment = d.get('aShapeComment', d.get('aPropertyComment', ''))
            label = d.get('aPropertyLabel', '')
            definedByNS = d.get('aPropertyDefinedBy', '')
            requiredIn = d.get('aMinCount', '')
            required = "No"
            if(int(requiredIn) > 0):
                required = "Yes" 
            counter += 1
            #yield (group, simp.simplify(d['aProperty']), comment, label, definedByNS, required)
            yield (simp.simplify(group, namespace=False), simp.simplify(d['aProperty']), comment, label, definedByNS, required, simp.simplify(d.get('aType', DEFAULT_TYPE)), simp.simplify(d.get('aDataType', DEFAULT_TYPE)) )
        #print(str(counter))

    def get_namespace(self):
        """Returns an iterator of tuples in the form (group, simplifed_property, description)"""
        """Filters for the novel DHS-DCAT attribute namespace using a partial string defined below"""
        simp = Simplifier(self.g)
        namespaceStr = 'dcat-tool'
        counterb = 0
        for d in self.get_query_dict():
            definedByNS = d.get('aPropertyDefinedBy', '')
            if(namespaceStr in definedByNS):
                group = d.get('aGroup', '')
                if(group == ''):
                    continue
                comment = d.get('aShapeComment', d.get('aPropertyComment', ''))
                label = d.get('aPropertyLabel', '')
                definedByNS = d.get('aPropertyDefinedBy', '')
                requiredIn = d.get('aMinCount', '')
                required = "No"
                if(int(requiredIn) > 0):
                    required = "Yes"
                counterb += 1
                yield (group, simp.simplify(d['aProperty']), comment, label, definedByNS, required, simp.simplify(d.get('aType', DEFAULT_TYPE)), simp.simplify(d.get('aDataType', DEFAULT_TYPE)) )
        #print(str(counterb))

    def get_template_column_info_objs(self):
        # g2 is an output graph of the terms in the collection instrument
        g2 = self.cleanGraph()

        self.ci_objs = []
        simp = Simplifier(self.g)
        for d in self.get_query_dict():
            try:
                title = d['aTitle']
            except KeyError:
                title = simp.simplify(d['aProperty'], namespace=False)

            # For the comment, grab the shape comment if it is present. otherwise, grab the property comment.
            # The comment goes into the tooltip for the column
            comment = d.get('aShapeComment', d.get('aPropertyComment', ''))
            if not comment:
                print("Need description for",d['aProperty'])

            obj = easy_workbook.ColumnInfo(value = title, # what is displayed in cell
                                           comment = title + ":\n" + comment,
                                           property = d['aProperty'],
                                           author = simp.simplify(d['aProperty']),
                                           width = int(d.get('aWidth',DEFAULT_WIDTH)),
                                           typ = simp.simplify(d.get('aDataType', DEFAULT_TYPE)),
                                           group = d.get('aGroup',''),
                                           )

            # Add the object to the column list and the graph
            self.ci_objs.append( obj )
            self.augmentGraph( g2, d )
        self.g2 = g2

    def validate(self, obj):
        """Check the dictionary (a loaded JSON object) """
        # -- setup a default date incase a date is bad or missing --
        today = datetime.today()

        # -- a list of fields that can show up as comma seaparted and need to be converted to an array --
        arrayFields = ['keyword', 'restrictionReason', 'primaryITInvestmentUII', 'fismaID', 'references', 'sharingAgreements', 'sourceDatasets', 'destinationDatasets', 'vendor', 'collectionAuthority', 'retentionAuthority', 'releaseAuthority', 'hostingLocation', 'theme', 'license']

        # -------------------------- create reference graph 's' and python dict (start) -----------------------------------
        # -- here we read the reference graph in and create a (pretty) python dict to look up properties --
        s =  Graph().parse(COLLECT_TTL)

        ALL_QUERY_2 = """
        SELECT DISTINCT ?aProperty ?aMinCount ?aDataType
        WHERE {
        {
        dhs:dataInventoryRecordShape sh:property ?aShapeName .
        ?aShapeName sh:path ?aProperty .
        OPTIONAL { ?aShapeName sh:minCount     ?aMinCount . }
        OPTIONAL { ?aShapeName sh:datatype     ?aDataType . }
        }
        UNION 
        {
        dhs:characteristicsShape sh:property ?aShapeName .
        ?aShapeName sh:path ?aProperty .
        OPTIONAL { ?aShapeName sh:minCount     ?aMinCount . }
        OPTIONAL { ?aShapeName sh:datatype     ?aDataType . }
        }
        }
        """

        allNodesDict = {}
        allNodes = s.query(ALL_QUERY_2)
        for row in allNodes:
            if(row[0].rfind('#')>0):
                start = row[0].rfind('#')
            else:
                start = row[0].rfind('/')
            rowDict = {"uri":str(row[0]),"min":str(row[1]),"type":str(row[2])}
            allNodesDict.update({row[0][start+1:]:rowDict})
        # -------------------------- create reference graph 's' and python dict (end) -----------------------------------

        #print(allNodesDict)

        # -- if the provided attribute is potentially an array check to see if it is comma delimited and if so return a list (array) --
        def checkArray(attValue):
            testSplit = attValue.split(',')
            if len(testSplit) > 1:
                #return '[' + attValue + ']'
                return testSplit
            else:
                return False

        # bind namespace to the graph or its namespace manager
        USG = Namespace('http://resources.data.gov/resources/dcat-us/#')
        DCTERMS = Namespace('http://purl.org/dc/terms/')
        DCAT = Namespace('http://www.w3.org/ns/dcat#')
        DHS = Namespace('https://usdhs.github.io/dcat-tool/#')

        # -- create an empty graph --
        dipr = Graph()

        # Bind a few prefix, namespace pairs to the graph
        dipr.bind("dc", DC)
        dipr.bind("usg", USG)
        dipr.bind("xsd", XSD)
        dipr.bind("dcterms", DCTERMS)
        dipr.bind("dcat", DCAT)
        dipr.bind("dhs", DHS)

        # Generate a empty node --  a GUID is generated as the node ID -- you can also create your own node id but it is not required or necessary 
        bnode = BNode()

        # -- set the rdf type to 'DataInventoryRecord'
        dipr.add( ( bnode, RDF.type, DHS.DataInventoryRecord))  

        for item in obj:
            items = item.split(':')
            #print(items[0] + '... ' + items[1])
            # -- look up the type and namespace(URI) in the allNodesDict --
            thisAttrProp = allNodesDict.get(items[1])
            #print(thisAttrProp['uri'])
            #print(thisAttrProp['type'])
            # -- if type is None just treat it like a string --
            if thisAttrProp['type'] == 'None' or thisAttrProp['type'] == 'http://www.w3.org/2001/XMLSchema#string':
                # -- if the item is a string (or undefined) type check to see if it is comma delimited --
                # TODO - this will probably NOT behave correctly for large text fields, so maybe skip title and description and a few others? 
                if items[1] in arrayFields:
                    arrayedVal = checkArray(obj[item])
                    if not arrayedVal:
                        dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(obj[item])) )
                    else:
                        #print(arrayedVal)
                        for arrayItem in arrayedVal:
                            dipr.add( ( bnode, URIRef(thisAttrProp['uri']), (Literal(arrayItem))) )
                else:    
                    dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(obj[item])) )
            # -- dates need to be converted to ensure ISO format ---
            elif thisAttrProp['type'] == 'http://www.w3.org/2001/XMLSchema#date':
                #print('A Date!!!')
                # TODO probably should be a try catch here... (and this is not needed in MySQL?)
                try:
                    dt = parse(obj[item])
                #print(dt)
                except:
                    dt = today
                #print(dt.strftime('%Y-%m-%d'))
                dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(dt.strftime('%Y-%m-%d'), datatype=XSD.date)) )
            # -- if there is a URL in a string field (won't be a anyURI type here) -- cap it with <> --
            elif thisAttrProp['type'] == 'http://www.w3.org/2001/XMLSchema#anyURI':
                #print('A URL!!!')
                if obj[item][:4] == 'http':
                    #print('A URL: ' + obj[item])
                    cappedURL = '<' + obj[item] + '>'
                    dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(cappedURL, datatype=XSD.anyURI) ))
                else:
                    dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(obj[item])))

            # -- handle 'characteristics' --
            elif items[1][:3] == 'ch-':
                #print('a characteristic! ' + items[1] )
                # --  see if the characteristicBnode is present in the graph --  
                if (None, RDF.type, DHS.Characteristics) not in dipr:
                    # -- create a bnode reference for Characteristic class -- 
                    characteristicBNode = BNode()
                    # -- set the rdf type to 'Characteristics' class and add it to the graph -- 
                    dipr.add( ( characteristicBNode, RDF.type, DHS.Characteristics))
                    # -- add dhs:characteristic as a node to the main graph --
                    characteristicAttrProp = allNodesDict.get("characteristics")
                    # - Yes! - adds a reference node to the primary graph - so the primary record knows there is a 'nested' node set --
                    dipr.add( ( bnode, URIRef(characteristicAttrProp['uri']), characteristicBNode))


                dipr.add( (characteristicBNode, URIRef(thisAttrProp['uri']), Literal(obj[item], datatype=URIRef(thisAttrProp['type']))) )
            else:
                dipr.add( ( bnode, URIRef(thisAttrProp['uri']), Literal(obj[item], datatype=URIRef(thisAttrProp['type']))) )
            #print(recroot[item])

        print("---------rdf/xml-----------")
        print(dipr.serialize( format='xml'))
        # print("---------rdf/xml-----------")
        # print(dipr.serialize( format='json-ld'))

        conforms, report, message = validate(dipr, shacl_graph=s, advanced=True, debug=False)

        if conforms == False:
            raise ValidationFail(message)
        
        return True

    def add_row(self, obj):
        """Validates a single object."""
        self.validate( obj )

        ident = obj['dcterms:identifier']
        if ident in self.seenIDs:
            raise ValidationFail(f'dcterms:identifier "{ident}" already seen')
        self.seenIDs.add(ident)
        self.rows.append( obj )



def validate_inventory_records( v, records ):
    ret = {}
    ret['response'] = 200       # looks good
    ret['records']  = []
    ret['messages'] = []
    ret['errors']   = []
    v.clear()
    for num, record in enumerate(records):
        ret['records'].append(record)
        try:
            v.validate(record)
            ret['messages'].append('OK')
        except ValidationFail as e:
            ret['response'] = 409
            ret['errors'].append(num)
            ret['messages'].append(str(e))

    return ret

def read_xlsx(fname) :
    tr = template_reader.TemplateReader( fname )
    return list(tr.inventory_records())

def validate_xlsx( v, fname):
    # validate_inventory_records( v, read_xlsx( fname ) )
    # v.get_template_column_info_objs()
    # #print(v.g2.serialize(format="xml"))
    # print(v.g2.serialize(format="json-ld"))
    return validate_inventory_records( v, read_xlsx( fname ) )
