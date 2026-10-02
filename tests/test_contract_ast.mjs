import assert from 'node:assert/strict';
import fs from 'node:fs';
import {test} from 'node:test';
import ts from '../integration/lan-probe/node_modules/typescript/lib/typescript.js';

const domain=fs.readFileSync(new URL('../contracts/api-v1.ts',import.meta.url),'utf8');
const parsed=ts.createSourceFile('api-v1.ts',domain,ts.ScriptTarget.Latest,true);
const statements=new Map(parsed.statements.filter(s=>s.name).map(s=>[s.name.text,s]));
const schemas=JSON.parse(fs.readFileSync(new URL('../contracts/openapi.json',import.meta.url))).components.schemas;
function fields(name){
  const declaration=statements.get(name);
  assert.ok(declaration,`missing domain interface ${name}`);
  const direct=declaration.members.map(m=>m.name.text);
  const inherited=(declaration.heritageClauses??[]).flatMap(c=>c.types.flatMap(t=>fields(t.expression.text)));
  return [...inherited,...direct].sort();
}
function literals(node){
  if(ts.isUnionTypeNode(node))return node.types.flatMap(literals);
  if(ts.isLiteralTypeNode(node))return [node.literal.text??node.literal.getText(parsed)];
  throw new Error(`Not a literal enum: ${node.getText(parsed)}`);
}
function enums(schema){
  if(schema.enum)return schema.enum.map(String).sort();
  const literal=(schema.anyOf??[]).find(s=>s.enum);
  assert.ok(literal,'Schema enum absent');
  return literal.enum.map(String).sort();
}
test('frozen core domain interface fields match actual OpenAPI output models',()=>{
  for(const name of ['State','Station','Track','Zone','Train','PhysicalLocation','WagonGroup','Resource',
    'Operation','Incident','Conflict','Efficiency','PlanSummary','PlanDetail','ReplanJob','Config',
    'CommandEnvelope','IncidentInput','HistoryEvent']){
    assert.deepEqual(fields(name),Object.keys(schemas[name].properties).sort(),name);
  }
});
test('all named domain enum sets match actual backend including type-independent train categories',()=>{
  const mapping={Role:['User','role'],Mode:['State','mode'],TrainType:['Train','type'],Direction:['Train','direction'],
    ServiceProfileId:['Train','service_profile_id'],ConsistKind:['Train','consist_kind'],TractionKind:['Train','traction_kind'],
    ProcessingKind:['Train','processing_kind'],TrainStatus:['Train','status'],OperationKind:['Operation','kind'],
    OperationStatus:['Operation','status'],ExecutionMode:['Operation','execution_mode'],ShuntPhase:['Operation','phase'],
    TrackAvailability:['Track','availability'],ResourceKind:['Resource','kind'],ResourceStatus:['Resource','status'],
    PlanStatus:['PlanSummary','status'],PlanValidity:['PlanSummary','validity'],ReplanStatus:['ReplanJob','status'],
    IncidentKind:['Incident','kind'],IncidentStatus:['Incident','status'],ConflictKind:['Conflict','kind'],
    Severity:['Conflict','severity'],EfficiencyCategory:['Efficiency','category']};
  for(const [name,[model,field]] of Object.entries(mapping)){
    assert.deepEqual(literals(statements.get(name).type).sort(),enums(schemas[model].properties[field]),name);
  }
  assert.deepEqual(enums(schemas.State.properties.speed),['1','10','5']);
});
