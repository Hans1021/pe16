/-
  PE16 ISA: executable Lean model. Mirrors isa/pe16.py (the Python golden model)
  field for field. Decoding uses / and % by constants so that `omega` can discharge
  the arithmetic side conditions in the proofs.
-/
namespace Pe16

structure Cfg where
  poly      : Nat
  crcw      : Nat      -- CRC width minus one (0..15)
  osrLsb    : Bool
  isrLsb    : Bool
  stuffN    : Nat      -- 0 disables stuffing
  stuffOnes : Bool     -- true: count ones only (USB); false: identical bits (CAN)
  nrzi      : Bool     -- NRZI line coding: data 0 = toggle
  diff      : Bool     -- OUT also drives pin i+1 with the complement

structure Stf where
  last : Bool
  run  : Nat

structure St where
  pc : Nat
  x : Nat
  y : Nat
  osr : Nat
  isr : Nat
  crc : Nat
  pout : Nat
  poe : Nat
  b : Bool
  s : Bool
  ov : Bool
  since : Nat
  stf : Stf
  halt : Bool
  txf : List Nat
  rxf : List Nat
  rxl : Bool           -- last sampled line level (NRZI decode)

def tb (v i : Nat) : Bool := v / 2 ^ i % 2 == 1

/-- write bit i of v (v assumed < 2^16) -/
def wb (v i : Nat) (bit : Bool) : Nat :=
  (if tb v i then v - 2 ^ i else v) + (if bit then 2 ^ i else 0)

def mask (c : Cfg) : Nat := 2 ^ (c.crcw + 1) - 1

def crcStep (c : Cfg) (crc : Nat) (d : Bool) : Nat :=
  let fb := tb crc c.crcw != d
  let sh := (crc * 2) % 2 ^ (c.crcw + 1)
  if fb then sh ^^^ (c.poly &&& mask c) else sh

def osrBit (c : Cfg) (osr : Nat) : Bool := if c.osrLsb then tb osr 0 else tb osr 15
def osrShift (c : Cfg) (osr : Nat) : Nat := if c.osrLsb then osr / 2 else (osr * 2) % 65536
def isrShift (c : Cfg) (isr : Nat) (d : Bool) : Nat :=
  if c.isrLsb then isr / 2 + (if d then 32768 else 0)
  else (isr * 2) % 65536 + (if d then 1 else 0)

/-! ### Bit-stuffing unit (shared by OUT stuff and IN destuff) -/
def due (c : Cfg) (t : Stf) : Bool := c.stuffN != 0 && decide (t.run ≥ c.stuffN)
def stuffBit (c : Cfg) (t : Stf) : Bool := if c.stuffOnes then false else !t.last
def afterStuff (c : Cfg) (t : Stf) : Stf :=
  { last := stuffBit c t, run := if c.stuffOnes then 0 else 1 }
def upd (c : Cfg) (t : Stf) (d : Bool) : Stf :=
  if c.stuffOnes then { last := d, run := if d then min (t.run + 1) 7 else 0 }
  else { last := d, run := if d == t.last then min (t.run + 1) 7 else 1 }

/-- One OUT-stuff emission: (line bit, data bit consumed?, new stuffer state). -/
def txStuff (c : Cfg) (t : Stf) (d : Bool) : Bool × Bool × Stf :=
  if due c t then (stuffBit c t, false, afterStuff c t) else (d, true, upd c t d)

/-- One IN-destuff sample: (data bit accepted?, new stuffer state). -/
def rxDestuff (c : Cfg) (t : Stf) (l : Bool) : Bool × Stf :=
  if due c t then (false, { last := l, run := if c.stuffOnes then 0 else 1 }) else (true, upd c t l)

/-- bit-reverse a 16-bit value -/
def rev16 (v : Nat) : Nat := (List.range 16).foldl (fun acc j => acc + (if tb v j then 2 ^ (15 - j) else 0)) 0

structure Res where
  st : St
  txPop : Bool
  rxPush : Option Nat

def rdReg (s : St) (pins r : Nat) : Nat :=
  match r with
  | 0 => s.x | 1 => s.y | 2 => s.osr | 3 => s.isr | 4 => s.crc
  | 5 => pins % 256 | 6 => s.poe | _ => 0

/-- Execute one instruction word for one cycle (FIFO host side handled in `step`). -/
def exec (c : Cfg) (ins pins : Nat) (s : St) : Res :=
  let sn := min (s.since + 1) 65535
  let nxt := (s.pc + 1) % 256
  let base : St := { s with since := sn }
  let op := ins / 8192
  let r := ins % 8192
  if s.halt then ⟨base, false, none⟩ else
  match op with
  | 0 =>
    let cnd := r / 1024 % 8 + 8 * (r / 512 % 2)
    let a := r % 256
    let take := match cnd with
      | 0 => true | 1 => s.x == 0 | 2 => s.x != 0 | 3 => s.y == 0 | 4 => s.y != 0
      | 5 => s.b | 6 => s.s | 7 => s.crc == 0 | 8 => s.x != s.y
      | 9 => due c s.stf | 10 => s.txf.isEmpty | _ => false
    let x' := if cnd == 2 then (s.x + 65535) % 65536 else s.x
    let y' := if cnd == 4 then (s.y + 65535) % 65536 else s.y
    ⟨{ base with x := x', y := y', pc := if take then a else nxt }, false, none⟩
  | 1 =>
    let pol := r / 4096 % 2 == 1
    let i := r / 512 % 8
    ⟨{ base with pc := if tb pins i == pol then nxt else s.pc }, false, none⟩
  | 2 =>
    let k0 := if r / 4096 % 2 == 1 then s.x else r % 4096
    let k := max k0 1
    if s.since + 1 ≥ k then
      ⟨{ base with pc := nxt, since := 0, ov := s.ov || decide (s.since + 1 > k) }, false, none⟩
    else ⟨{ base with pc := s.pc }, false, none⟩
  | 3 =>
    let m := r / 2048 % 4
    let i := r / 256 % 8
    let d := osrBit c s.osr
    if m == 3 then
      ⟨{ base with poe := wb s.poe i d, osr := osrShift c s.osr, b := d, pc := nxt }, false, none⟩
    else
      let (v, cons, t') := if m == 2 then txStuff c s.stf d else (d, true, s.stf)
      let cur := tb s.pout i
      let line := if c.nrzi then (if v then cur else !cur) else v
      let po1 := wb s.pout i line
      let po := if c.diff then wb po1 ((i + 1) % 8) (!line) else po1
      if cons then
        ⟨{ base with pout := po, b := v, osr := osrShift c s.osr, s := false,
                     crc := if m == 0 then s.crc else crcStep c s.crc d, stf := t', pc := nxt }, false, none⟩
      else
        ⟨{ base with pout := po, b := v, s := true, stf := t', pc := nxt }, false, none⟩
  | 4 =>
    let m := r / 2048 % 4
    let i := r / 256 % 8
    let l := tb pins i
    let d := if c.nrzi then l == s.rxl else l
    if m == 3 then ⟨{ base with b := l, pc := nxt }, false, none⟩ else
    let base := { base with rxl := l }
    let (acc, t') := if m == 2 then rxDestuff c s.stf d else (true, s.stf)
    if acc then
      ⟨{ base with b := d, isr := isrShift c s.isr d, s := false,
                   crc := if m == 0 then s.crc else crcStep c s.crc d, stf := t', pc := nxt }, false, none⟩
    else ⟨{ base with b := d, s := true, stf := t', pc := nxt }, false, none⟩
  | 5 =>
    let dst := r / 1024 % 8
    let v := r % 1024
    let i := v / 128 % 8
    let bv := v % 2 == 1
    match dst with
    | 0 => ⟨{ base with pout := wb s.pout i bv, pc := nxt }, false, none⟩
    | 1 => ⟨{ base with poe := wb s.poe i bv, pc := nxt }, false, none⟩
    | 2 => ⟨{ base with x := v, pc := nxt }, false, none⟩
    | 3 => ⟨{ base with y := v, pc := nxt }, false, none⟩
    | 4 =>
      let crc' := if tb v 1 then mask c else if tb v 0 then 0 else s.crc
      let stf' : Stf := if tb v 2 then ⟨false, 0⟩ else s.stf
      ⟨{ base with crc := crc', stf := stf', isr := if tb v 3 then 0 else s.isr,
                   ov := if tb v 4 then false else s.ov, since := if tb v 5 then 0 else sn,
                   pc := nxt }, false, none⟩
    | 5 => ⟨{ base with pout := v % 256, pc := nxt }, false, none⟩
    | 6 => ⟨{ base with poe := v % 256, pc := nxt }, false, none⟩
    | _ => ⟨{ base with pc := nxt }, false, none⟩
  | 6 =>
    let dst := r / 1024 % 8
    let v0 := rdReg s pins (r / 128 % 8)
    let v1 := if r / 64 % 2 == 1 then 65535 - v0 else v0
    let sh := 15 - c.crcw
    let v2 := if r / 32 % 2 == 1 then (v1 * 2 ^ sh + (2 ^ sh - 1)) % 65536 else v1
    let v := if r / 16 % 2 == 1 then rev16 v2 else v2
    match dst with
    | 0 => ⟨{ base with x := v, pc := nxt }, false, none⟩
    | 1 => ⟨{ base with y := v, pc := nxt }, false, none⟩
    | 2 => ⟨{ base with osr := v, pc := nxt }, false, none⟩
    | 3 => ⟨{ base with isr := v, pc := nxt }, false, none⟩
    | 4 => ⟨{ base with crc := v &&& mask c, pc := nxt }, false, none⟩
    | 5 => ⟨{ base with pout := v % 256, pc := nxt }, false, none⟩
    | 6 => ⟨{ base with poe := v % 256, pc := nxt }, false, none⟩
    | _ => ⟨{ base with pc := nxt }, false, none⟩
  | _ =>
    match r / 2048 % 4 with
    | 0 => match s.txf with
      | w :: _ => ⟨{ base with osr := w, pc := nxt }, true, none⟩
      | [] => ⟨base, false, none⟩
    | 1 => if s.rxf.length < 4 then ⟨{ base with pc := nxt }, false, some s.isr⟩ else ⟨base, false, none⟩
    | 2 => ⟨{ base with halt := true }, false, none⟩
    | _ => ⟨{ base with pc := nxt }, false, none⟩

/-- One rising clock edge: engine + host FIFO accesses (both see start-of-cycle state). -/
def step (c : Cfg) (imem : Nat → Nat) (pins : Nat) (push : Option Nat) (hpop : Bool) (s : St) : St :=
  let r := exec c (imem s.pc) pins s
  let tx1 := if r.txPop then s.txf.tail else s.txf
  let tx2 := match push with
    | some v => if s.txf.length < 4 then tx1 ++ [v % 65536] else tx1
    | none => tx1
  let rx1 := if hpop then s.rxf.tail else s.rxf
  let rx2 := match r.rxPush with | some v => rx1 ++ [v] | none => rx1
  { r.st with txf := tx2, rxf := rx2 }

end Pe16
