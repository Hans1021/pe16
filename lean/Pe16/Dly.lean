import Pe16.Isa
/-! T1/T2: deadline timing of DLY. -/
namespace Pe16

def dlyWord (k : Nat) : Nat := 16384 + k

/-- Input streams: pins, host push, host pop per cycle. -/
structure Inp where
  pins : Nat → Nat
  push : Nat → Option Nat
  pop  : Nat → Bool

def run (c : Cfg) (imem : Nat → Nat) (i : Inp) : Nat → Nat → St → St
  | 0, _, s => s
  | n + 1, t, s => run c imem i n (t + 1) (step c imem (i.pins t) (i.push t) (i.pop t) s)

theorem step_pc (c imem p u h s) : (step c imem p u h s).pc = (exec c (imem s.pc) p s).st.pc := rfl
theorem step_since (c imem p u h s) : (step c imem p u h s).since = (exec c (imem s.pc) p s).st.since := rfl
theorem step_ov (c imem p u h s) : (step c imem p u h s).ov = (exec c (imem s.pc) p s).st.ov := rfl
theorem step_halt (c imem p u h s) : (step c imem p u h s).halt = (exec c (imem s.pc) p s).st.halt := rfl

theorem exec_dly (c : Cfg) (k pins : Nat) (s : St) (hk1 : 1 ≤ k) (hk : k < 4096)
    (hh : s.halt = false) (hs : s.since + 1 ≤ k) :
    let r := (exec c (dlyWord k) pins s).st
    r.halt = false ∧ r.ov = s.ov ∧
    (if s.since + 1 = k then r.pc = (s.pc + 1) % 256 ∧ r.since = 0
     else r.pc = s.pc ∧ r.since = s.since + 1) := by
  have h1 : dlyWord k / 8192 = 2 := by unfold dlyWord; omega
  have h2 : dlyWord k % 8192 / 4096 % 2 = 0 := by unfold dlyWord; omega
  have h3 : dlyWord k % 8192 % 4096 = k := by unfold dlyWord; omega
  have h4 : max k 1 = k := by omega
  have h5 : min (s.since + 1) 65535 = s.since + 1 := by omega
  simp only [exec, hh, h1, h2, h3, h4, h5]
  by_cases he : s.since + 1 = k
  · simp [he, h4]
  · have : ¬ (k ≤ s.since + 1) := by omega
    simp [he, this, h4]

/-- T1: from arrival at `DLY k` with `since = c < k`, the engine waits and retires
    after exactly `k - c` cycles, whatever the pin and FIFO inputs are. It leaves
    `since = 0` and does not set the overrun flag. -/
theorem dly_retire (c : Cfg) (imem : Nat → Nat) (k : Nat) (hk1 : 1 ≤ k) (hk : k < 4096) :
    ∀ n (i : Inp) (t : Nat) (s : St), s.halt = false → imem s.pc = dlyWord k →
      s.since + 1 + n = k →
      (run c imem i (n + 1) t s).pc = (s.pc + 1) % 256 ∧
      (run c imem i (n + 1) t s).since = 0 ∧
      (run c imem i (n + 1) t s).ov = s.ov := by
  intro n
  induction n with
  | zero =>
    intro i t s hh hi hn
    obtain ⟨_, hov, hr⟩ := exec_dly c k (i.pins t) s hk1 hk hh (by omega)
    simp only [show s.since + 1 = k by omega, if_true] at hr
    simp only [run, step_pc, step_since, step_ov, hi]
    exact ⟨hr.1, hr.2, hov⟩
  | succ n ih =>
    intro i t s hh hi hn
    obtain ⟨hha, hov, hr⟩ := exec_dly c k (i.pins t) s hk1 hk hh (by omega)
    simp only [show ¬ (s.since + 1 = k) by omega, if_false] at hr
    let s' := step c imem (i.pins t) (i.push t) (i.pop t) s
    have e1 : s'.pc = s.pc := by simp only [s', step_pc, hi]; exact hr.1
    have e2 : s'.since = s.since + 1 := by simp only [s', step_since, hi]; exact hr.2
    have e3 : s'.ov = s.ov := by simp only [s', step_ov, hi]; exact hov
    have e4 : s'.halt = false := by simp only [s', step_halt, hi]; exact hha
    have := ih i (t + 1) s' e4 (by rw [e1]; exact hi) (by omega)
    show (run c imem i (n + 1) (t + 1) s').pc = _ ∧ _ ∧ _
    rw [e1, e3] at this
    exact this

/-- T2 (period): if the previous DLY retired leaving `since = 0` and the loop body
    reaches `DLY k` after `m < k` cycles (so `since = m` on arrival; T3 below shows
    each non-timing instruction adds exactly one), the next retire is `k - m` cycles
    later, i.e. exactly `k` cycles after the previous one. -/
theorem dly_period (c : Cfg) (imem : Nat → Nat) (k m : Nat) (hk1 : 1 ≤ k) (hk : k < 4096)
    (hm : m < k) (i : Inp) (t : Nat) (s : St) (hh : s.halt = false)
    (hi : imem s.pc = dlyWord k) (hs : s.since = m) :
    m + (k - m) = k ∧ (run c imem i (k - m) t s).since = 0 ∧
    (run c imem i (k - m) t s).pc = (s.pc + 1) % 256 := by
  have := dly_retire c imem k hk1 hk (k - m - 1) i t s hh hi (by omega)
  have e : k - m - 1 + 1 = k - m := by omega
  rw [e] at this
  exact ⟨by omega, this.2.1, this.1⟩

/-- T3: every instruction other than DLY, and other than `SET CTL` with the sync bit,
    increments `since` by exactly one (saturating at 65535). -/
theorem since_incr (c : Cfg) (ins pins : Nat) (s : St)
    (hop : ins / 8192 ≠ 2)
    (hsync : ¬ (ins / 8192 = 5 ∧ ins % 8192 / 1024 % 8 = 4 ∧ tb (ins % 8192 % 1024) 5 = true)) :
    (exec c ins pins s).st.since = min (s.since + 1) 65535 := by
  unfold exec
  split
  · rfl
  · generalize ins / 8192 = op at *
    rcases op with _|_|_|_|_|_|_|op <;> simp only at hop hsync ⊢ <;> (try contradiction) <;>
      (repeat' split) <;> simp_all
end Pe16
