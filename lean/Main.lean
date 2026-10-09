import Pe16.Isa
open Pe16
/-! Cross-check: replay traces produced by the Python golden model (isa/pe16.py) and
    compare all 17 state fields after every cycle. -/

def bn (b : Bool) : Nat := if b then 1 else 0

def fields (s : St) : List Nat :=
  [s.pc, s.x, s.y, s.osr, s.isr, s.crc, s.pout, s.poe, bn s.b, bn s.s, bn s.ov, s.since,
   bn s.stf.last, s.stf.run, bn s.halt, s.txf.length, s.rxf.length, bn s.rxl]

def st0 : St := ⟨0, 0, 0, 0, 0, 0, 0, 0, false, false, false, 0, ⟨false, 0⟩, false, [], [], false⟩

def nums (l : String) : List Nat :=
  ((l.splitOn " ").drop 1).filterMap fun w => if w.startsWith "-" then some 999999 else w.toNat?

def main : IO UInt32 := do
  let stdin ← IO.getStdin
  let mut cfg : Cfg := ⟨0, 15, true, true, 0, false, false, false⟩
  let mut prog : Array Nat := #[]
  let mut st := st0
  let mut cases := 0
  let mut cycles := 0
  let mut bad := 0
  repeat
    let line ← stdin.getLine
    if line.isEmpty then break
    let l := line.trimRight
    let v := nums l
    if l.startsWith "C" then
      cfg := ⟨v[0]!, v[1]!, v[2]! == 1, v[3]! == 1, v[4]!, v[5]! == 1, v[6]! == 1, v[7]! == 1⟩
      st := st0; cases := cases + 1
    else if l.startsWith "P" then
      prog := v.toArray
    else if l.startsWith "S" then
      let pins := v[0]!
      let push := if v[1]! == 999999 then none else some v[1]!
      let pop := v[2]! == 1
      let imem := fun a => prog.getD a 0
      st := step cfg imem pins push pop st
      cycles := cycles + 1
      let exp := v.drop 3
      if fields st != exp then
        bad := bad + 1
        if bad ≤ 3 then
          IO.println s!"MISMATCH case {cases} cycle {cycles}: lean {fields st} python {exp}"
  IO.println s!"cases={cases} cycles={cycles} mismatches={bad}"
  return (if bad == 0 then 0 else 1)
