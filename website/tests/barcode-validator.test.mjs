import test from 'node:test';
import assert from 'node:assert/strict';
import {validateBarcode,checkDigit} from '../src/lib/barcode-validator.mjs';
test('known valid GTIN examples keep their leading zeros',()=>{
  for(const [code,format] of [['96385074','EAN-8'],['012345678905','UPC-A'],['3017620422003','EAN-13'],['03017620422003','GTIN-14']]) {
    const result=validateBarcode(code);assert.equal(result.valid,true,code);assert.equal(result.format,format);assert.equal(result.value,code);assert.equal(result.gtin14,code.padStart(14,'0'));
  }
  assert.ok(validateBarcode('012345678905').equivalents.includes('0012345678905'));
});
test('invalid checksum identifies expected digit without silently correcting input',()=>{
  const result=validateBarcode('3017620422004');assert.equal(result.valid,false);assert.equal(result.expected,'3');assert.equal(result.value,'3017620422004');
});
test('unsupported input is rejected rather than coerced into a different identifier',()=>{
  for(const code of ['',null,123456789012,'123','3017 620422003','3017620422003e0','３０１７６２０４２２００３','<script>']) assert.equal(validateBarcode(code).valid,false,String(code));
  assert.equal(validateBarcode(' 3017620422003 ').valid,true);
});
test('a single-digit error in a valid number is detected at every position',()=>{
  const code='3017620422003';
  for(let i=0;i<code.length;i++) assert.equal(validateBarcode(code.slice(0,i)+String((Number(code[i])+1)%10)+code.slice(i+1)).valid,false);
  assert.equal(checkDigit('301762042200'),'3');
  assert.throws(()=>checkDigit('12a'));
});
