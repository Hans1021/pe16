import Pe16.Isa
/-! T4-T6: the bit-stuffing unit used by `OUT stuff` and `IN destuff`.
    `exec` calls `txStuff` and `rxDestuff` directly, so these results are about the model's
    datapath, not a separate abstraction. -/
namespace Pe16

/-- Apply one `OUT stuff` per presented bit; line history is newest-first. -/
def txSeq (c : Cfg) : Stf → List Bool → List Bool → Stf × List Bool
  | t, h, [] => (t, h)
  | t, h, d :: ds => match txStuff c t d with
    | (l, _, t') => txSeq c t' (l :: h) ds

/-- Length of the run of `a` at the head of a newest-first list. -/
def trail (a : Bool) : List Bool → Nat
  | [] => 0
  | x :: xs => if x == a then trail a xs + 1 else 0

/-- No run of equal bits anywhere in the history is longer than `n`. -/
def runsLe (n : Nat) : List Bool → Prop
  | [] => True
  | x :: xs => trail x (x :: xs) ≤ n ∧ runsLe n xs

def Inv (n : Nat) (t : Stf) (h : List Bool) : Prop :=
  t.run = trail t.last h ∧ t.run ≤ n ∧ runsLe n h ∧ (h = [] ∨ h.head? = some t.last)

theorem trail_ne (a : Bool) (h : List Bool) (b : Bool) (hb : b ≠ a)
    (hh : h = [] ∨ h.head? = some a) : trail b h = 0 := by
  rcases hh with rfl | hh
  · rfl
  · cases h with
    | nil => rfl
    | cons x xs =>
      simp at hh; subst hh; simp [trail, Ne.symm hb]

theorem txStuff_due (c : Cfg) (t : Stf) (d : Bool) (h : due c t = true) :
    txStuff c t d = (stuffBit c t, false, afterStuff c t) := by simp [txStuff, h]
theorem txStuff_ndue (c : Cfg) (t : Stf) (d : Bool) (h : due c t = false) :
    txStuff c t d = (d, true, upd c t d) := by simp [txStuff, h]

theorem inv_step (c : Cfg) (hc : c.stuffOnes = false) (n : Nat) (hn2 : 1 ≤ n) (hn7 : n ≤ 7)
    (hN : c.stuffN = n) (t : Stf) (h : List Bool) (d : Bool) (hi : Inv n t h) :
    Inv n (txStuff c t d).2.2 ((txStuff c t d).1 :: h) := by
  obtain ⟨hr, hle, hok, hhd⟩ := hi
  by_cases hd : t.run ≥ n
  · have hdue : due c t = true := by simp [due, hN]; omega
    rw [txStuff_due c t d hdue]
    have h0 : trail (!t.last) h = 0 := trail_ne t.last h _ (by cases t.last <;> simp) hhd
    simp only [afterStuff, stuffBit, hc, Bool.false_eq_true, if_false]
    refine ⟨?_, by dsimp only; omega, ⟨?_, hok⟩, Or.inr rfl⟩
    · simp [trail, h0]
    · simp [trail, h0]; omega
  · have hdue : due c t = false := by simp [due, hN]; omega
    rw [txStuff_ndue c t d hdue]
    simp only [upd, hc, Bool.false_eq_true, if_false]
    by_cases hdl : d = t.last
    · subst hdl
      have hm : min (t.run + 1) 7 = t.run + 1 := by omega
      simp only [beq_self_eq_true, if_true, hm]
      refine ⟨by simp [trail, hr], by dsimp only; omega, ⟨?_, hok⟩, Or.inr rfl⟩
      simp [trail, ← hr]; omega
    · have h0 : trail d h = 0 := trail_ne t.last h d hdl hhd
      have hb : (d == t.last) = false := by simp [hdl]
      simp only [hb, Bool.false_eq_true, if_false]
      refine ⟨by simp [trail, h0], by dsimp only; omega, ⟨?_, hok⟩, Or.inr rfl⟩
      simp [trail, h0]; omega

/-- T4 (CAN stuffing rule): starting from the reset state `SET CTL stf`, any sequence of
    `OUT stuff` executions, with any presented data bits, puts on the line no run of equal
    bits longer than `stuffN` (5 for CAN, so never six equal bits). -/
theorem no_long_runs (c : Cfg) (hc : c.stuffOnes = false) (hn2 : 1 ≤ c.stuffN) (hn7 : c.stuffN ≤ 7)
    (ds : List Bool) : runsLe c.stuffN (txSeq c ⟨false, 0⟩ [] ds).2 := by
  suffices ∀ ds t h, Inv c.stuffN t h → Inv c.stuffN (txSeq c t h ds).1 (txSeq c t h ds).2 by
    exact (this ds ⟨false, 0⟩ [] ⟨rfl, by simp, trivial, Or.inl rfl⟩).2.2.1
  intro ds
  induction ds with
  | nil => intro t h hi; exact hi
  | cons d ds ih =>
    intro t h hi
    have := inv_step c hc c.stuffN hn2 hn7 rfl t h d hi
    simp only [txSeq]
    exact ih _ _ this

/-- T5 (lock-step): if the transmitter's and receiver's stuffing units are in the same state,
    the receiver sampling the transmitted line bit accepts it exactly when the transmitter
    consumed a data bit, and both units move to the same next state. Holds in both modes and
    for every `stuffN`. -/
theorem lockstep (c : Cfg) (t : Stf) (d : Bool) :
    let tx := txStuff c t d
    rxDestuff c t tx.1 = (tx.2.1, tx.2.2) := by
  unfold txStuff rxDestuff afterStuff
  by_cases h : due c t = true <;> simp [h]

/-- T5 (round trip): the bits the receiver accepts from the transmitted line are exactly the
    data bits the transmitter consumed, in order. -/
def rxAccept (c : Cfg) : Stf → List Bool → List Bool
  | _, [] => []
  | t, l :: ls => match rxDestuff c t l with
    | (true, t') => l :: rxAccept c t' ls
    | (false, t') => rxAccept c t' ls

def txRun (c : Cfg) : Stf → List Bool → List Bool × List Bool   -- (line, consumed)
  | _, [] => ([], [])
  | t, d :: ds => match txStuff c t d with
    | (l, true, t') => let r := txRun c t' ds; (l :: r.1, d :: r.2)
    | (l, false, t') => let r := txRun c t' ds; (l :: r.1, r.2)

theorem roundtrip (c : Cfg) : ∀ (ds : List Bool) (t : Stf),
    rxAccept c t (txRun c t ds).1 = (txRun c t ds).2 := by
  intro ds
  induction ds with
  | nil => intro t; rfl
  | cons d ds ih =>
    intro t
    have hl := lockstep c t d
    simp only at hl
    cases hdue : due c t
    · rw [txStuff_ndue c t d hdue] at hl
      simp only [txRun, txStuff_ndue c t d hdue, rxAccept, hl, ih]
    · rw [txStuff_due c t d hdue] at hl
      simp only [txRun, txStuff_due c t d hdue, rxAccept, hl, ih]

/-- T6 (configuration hazard): with identical-bit stuffing and `stuffN = 1`, once a stuff bit
    is due it stays due, so `OUT stuff` never consumes another data bit (livelock). -/
theorem stuff1_livelock (c : Cfg) (hc : c.stuffOnes = false) (hn : c.stuffN = 1) (t : Stf)
    (_hd : due c t = true) : due c (afterStuff c t) = true := by
  simp [due, afterStuff, hn, hc]

/-- T6': for `stuffN ≥ 2` the state right after a stuff bit is never due, so each data bit
    costs at most one stuff bit. -/
theorem stuff_progress (c : Cfg) (hn : 2 ≤ c.stuffN) (t : Stf) : due c (afterStuff c t) = false := by
  unfold due afterStuff
  by_cases ho : c.stuffOnes <;> simp [ho] <;> omega

/-- USB-mode invariant: a nonzero run count implies the last line bit was 1. Every update
    of the stuffing unit (`upd`, `afterStuff`, the destuff branch, and the `stf` reset)
    preserves it. -/
def OnesInv (t : Stf) : Prop := t.run > 0 → t.last = true

theorem onesInv_upd (c : Cfg) (ho : c.stuffOnes = true) (t : Stf) (d : Bool) : OnesInv (upd c t d) := by
  unfold OnesInv upd; cases d <;> simp [ho]
theorem onesInv_after (c : Cfg) (ho : c.stuffOnes = true) (t : Stf) : OnesInv (afterStuff c t) := by
  simp [OnesInv, afterStuff, ho]
theorem onesInv_rx (c : Cfg) (ho : c.stuffOnes = true) (t : Stf) (l : Bool) :
    OnesInv (rxDestuff c t l).2 := by
  unfold rxDestuff; split
  · simp [OnesInv, ho]
  · exact onesInv_upd c ho t l
theorem onesInv_reset : OnesInv ⟨false, 0⟩ := by simp [OnesInv]

/-- T7: under the USB-mode invariant, the emitted stuff bit (0) equals `!last`. This proves
    that the surviving RTL mutant `pout <= ~last` is equivalent when the mode is fixed. -/
theorem ones_stuffbit (c : Cfg) (ho : c.stuffOnes = true) (t : Stf) (hi : OnesInv t)
    (hd : due c t = true) : stuffBit c t = !t.last := by
  have hr : t.run > 0 := by
    simp [due] at hd; omega
  simp [stuffBit, ho, hi hr]

end Pe16
