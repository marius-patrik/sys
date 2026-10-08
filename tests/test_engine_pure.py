import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'/'livingd'))
from livingd.database import SEED_GRAPHS
from livingd.logic import (GraphValidationError,validate_graph,due_nodes,node_inputs,execute_pure,selected_outputs)

class GraphTests(unittest.TestCase):
    def test_both_seed_graphs(self):
        for g in SEED_GRAPHS: self.assertEqual(len(validate_graph(g['definition'])),2)
    def test_uppercase_trace(self):
        graph=SEED_GRAPHS[1]['definition']
        self.assertEqual(due_nodes(graph,set(),set()),['upper'])
        args=node_inputs(graph,'upper',{'text':'hello'}, {})
        result=execute_pure('text.upper',args)
        self.assertEqual(result,{'value':'HELLO'})
        self.assertEqual(due_nodes(graph,{'upper'},set()),['view'])
        view=execute_pure('view.text',node_inputs(graph,'view',{'text':'hello'}, {'upper':result}))
        self.assertEqual(selected_outputs(graph,{'upper':result,'view':view}),{'view':{'type':'text','value':'HELLO'}})
    def test_parallel_and_join(self):
        graph={'inputs':{'text':'text'},'nodes':[
            {'id':'a','capability':'text.upper','inputs':{'value':{'input':'text'}}},
            {'id':'b','capability':'text.echo','inputs':{'value':{'input':'text'}}},
            {'id':'c','capability':'text.prefix','inputs':{'prefix':{'node':'a','port':'value'},'value':{'node':'b','port':'value'}}}
        ],'outputs':{'result':{'node':'c','port':'value'}}}
        self.assertEqual(due_nodes(graph,set(),set()),['a','b'])
        self.assertEqual(due_nodes(graph,{'a'},set()),['b'])
        self.assertEqual(due_nodes(graph,{'a','b'},set()),['c'])
    def test_cycle_rejected(self):
        graph={'inputs':{},'nodes':[
            {'id':'a','capability':'text.echo','inputs':{'value':{'node':'b','port':'value'}}},
            {'id':'b','capability':'text.echo','inputs':{'value':{'node':'a','port':'value'}}}
        ]}
        with self.assertRaisesRegex(GraphValidationError,'cycle'):validate_graph(graph)
    def test_bad_type_rejected(self):
        graph={'inputs':{'x':'boolean'},'nodes':[{'id':'a','capability':'text.echo','inputs':{'value':{'input':'x'}}}]}
        with self.assertRaisesRegex(GraphValidationError,'type mismatch'):validate_graph(graph)
    def test_unknown_capability_rejected(self):
        graph={'inputs':{},'nodes':[{'id':'x','capability':'oci.program','inputs':{}}]}
        with self.assertRaisesRegex(GraphValidationError,'unknown capability'):validate_graph(graph)
    def test_invalid_literal_rejected(self):
        graph={'inputs':{},'nodes':[{'id':'x','capability':'text.echo','inputs':{'value':{'literal':10}}}]}
        with self.assertRaisesRegex(GraphValidationError,'type mismatch'):validate_graph(graph)

if __name__ == '__main__':unittest.main()
