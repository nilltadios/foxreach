/-!
# Foxreach Core Access Policy & Boundary Primitives

Formalizes the low-level security predicates from:
- `extension/background.js` (`plainHost`, `inList`, `hostBlocked`, `siteListed`,
  `siteAllowed`, `checkUrl`, `ownerUrl`)
- `bridge/socket.mjs` (`assertPrivate`)
- `bridge/mcp.mjs` (`readable`, `checkSavePath`, Windows pipe challenge-response)
- `extension/content/content.js` (`stateOf` secret-value redaction)
-/

namespace Foxreach

/-! ## Hostnames, Subdomain Matching, and URLs -/

/-- A normalized DNS hostname split into lowercase non-empty labels,
e.g. `"login.bank.example"` is `["login", "bank", "example"]`. -/
abbrev Host := List String

/-- Strip trailing empty/dot labels (models `plainHost` in `extension/background.js:65`). -/
def stripTrailingEmpty (labels : List String) : List String :=
  (labels.reverse.dropWhile (· == "")).reverse

def asciiLowerChar (c : Char) : Char :=
  if 'A' ≤ c && c ≤ 'Z' then Char.ofNat (c.toNat + 32) else c

def asciiLower (s : String) : String :=
  String.ofList (s.toList.map asciiLowerChar)

def normalizeHost (raw : List String) : Host :=
  stripTrailingEmpty (raw.map asciiLower)

/-- `b` is a DNS-label suffix of `h` (models `host === b || host.endsWith("." + b)`
in `extension/background.js:76`). -/
def labelSuffix (b : Host) : Host → Bool
  | [] => b.isEmpty
  | x :: rest => (x :: rest == b) || labelSuffix b rest

/-- Exact match on non-empty hostnames and list entries (`extension/background.js:76`). -/
def hostMatches (host entry : Host) : Bool :=
  !host.isEmpty && !entry.isEmpty && labelSuffix entry host

def inList (host : Host) (entries : List Host) : Bool :=
  entries.any (hostMatches host)

theorem labelSuffix_self (h : Host) : labelSuffix h h = true := by
  cases h with
  | nil => rfl
  | cons x rest => simp [labelSuffix]

theorem labelSuffix_append (pre b : Host) : labelSuffix b (pre ++ b) = true := by
  induction pre with
  | nil => simpa using labelSuffix_self b
  | cons x xs ih =>
    simp [labelSuffix, ih]

theorem labelSuffix_iff_exists_prefix (b h : Host) :
    labelSuffix b h = true ↔ ∃ pre, h = pre ++ b := by
  induction h with
  | nil =>
    cases b with
    | nil =>
      constructor
      · intro _; exact ⟨[], rfl⟩
      · intro _; rfl
    | cons y ys =>
      constructor
      · intro hfalse; simp [labelSuffix] at hfalse
      · rintro ⟨pre, heq⟩
        cases pre <;> simp at heq
  | cons x rest ih =>
    constructor
    · intro hs
      simp [labelSuffix] at hs
      rcases hs with heq | hrest
      · exact ⟨[], by simpa using heq⟩
      · rcases ih.mp hrest with ⟨pre, rfl⟩
        exact ⟨x :: pre, rfl⟩
    · rintro ⟨pre, heq⟩
      cases pre with
      | nil =>
        simp at heq
        subst heq
        simp [labelSuffix]
      | cons p ps =>
        simp at heq
        rcases heq with ⟨rfl, rfl⟩
        simp [labelSuffix, ih.mpr ⟨ps, rfl⟩]

/-- Any host matches a list that directly contains it (`host ≠ []`). -/
theorem inList_mem {h : Host} {L : List Host} (hne : h ≠ []) (hmem : h ∈ L) :
    inList h L = true := by
  unfold inList
  rw [List.any_eq_true]
  refine ⟨h, hmem, ?_⟩
  have hEmpty : h.isEmpty = false := by
    cases h with
    | nil => contradiction
    | cons _ _ => rfl
  simp [hostMatches, hEmpty, labelSuffix_self]

/-- Subdomain closure: if `base` is in `L`, any subdomain `pre ++ base` is also matched. -/
theorem inList_subdomain {pre base : Host} {L : List Host}
    (hbase : inList base L = true) :
    inList (pre ++ base) L = true := by
  unfold inList at hbase ⊢
  rw [List.any_eq_true] at hbase ⊢
  rcases hbase with ⟨entry, hmem, hmatch⟩
  refine ⟨entry, hmem, ?_⟩
  simp only [hostMatches, Bool.and_eq_true, Bool.not_eq_true'] at hmatch ⊢
  rcases hmatch with ⟨⟨hBaseNE, hEntryNE⟩, hSuf⟩
  rw [labelSuffix_iff_exists_prefix] at hSuf
  rcases hSuf with ⟨p, rfl⟩
  have hAppNE : (pre ++ (p ++ entry)).isEmpty = false := by
    cases pre with
    | nil => exact hBaseNE
    | cons _ _ => rfl
  refine ⟨⟨hAppNE, hEntryNE⟩, ?_⟩
  rw [labelSuffix_iff_exists_prefix]
  exact ⟨pre ++ p, by simp [List.append_assoc]⟩

/-- Label-boundary safety: `"evilbank.example"` is NOT matched by `"bank.example"`. -/
theorem no_suffix_label_collision :
    inList ["evilbank", "example"] [["bank", "example"]] = false := by
  decide

/-- Trailing dots and uppercase normalize identically (`plainHost`, `extension/background.js:65`). -/
theorem normalize_trailing_dot_and_case :
    normalizeHost ["BANK", "Example", "", ""] = ["bank", "example"] := by
  decide

/-! ## URL Schemes & Site Policy (`extension/background.js:117-124, 236, 622-635`) -/

inductive Scheme where
  | http
  | https
  | aboutBlank
  | aboutOther
  | file
  | mozExtension
  | data
  | blob
  | other
  deriving DecidableEq, Repr

structure Url where
  scheme : Scheme
  host : Host
  deriving DecidableEq, Repr

/-- `isWeb(url) = /^https?:/.test(url)` (`extension/background.js:117`). -/
def isWeb (u : Url) : Bool :=
  u.scheme == .http || u.scheme == .https

inductive SiteAccess where
  | all
  | listed
  deriving DecidableEq, Repr

/-- `hostBlocked(url)` (`extension/background.js:78`). -/
def hostBlocked (blockedHosts : List Host) (u : Url) : Bool :=
  inList u.host blockedHosts

/-- `siteListed(url)` (`extension/background.js:236`). -/
def siteListed (siteAccess : SiteAccess) (allowedHosts : List Host) (u : Url) : Bool :=
  !isWeb u || siteAccess == .all || inList u.host allowedHosts

/-- `siteAllowed(url)` (`extension/background.js:121-124`). -/
def siteAllowed (siteAccess : SiteAccess) (allowedHosts : List Host)
    (browserGrants : Host → Bool) (u : Url) : Bool :=
  !isWeb u || (siteListed siteAccess allowedHosts u && browserGrants u.host)

/-- `checkUrl(url)` (`extension/background.js:622-635`). -/
def checkUrlOk (siteAccess : SiteAccess) (allowedHosts blockedHosts : List Host)
    (browserGrants : Host → Bool) (u : Url) : Bool :=
  (u.scheme == .http || u.scheme == .https || u.scheme == .aboutBlank) &&
  !hostBlocked blockedHosts u &&
  siteAllowed siteAccess allowedHosts browserGrants u

/-- Blocked hosts take strict precedence over `siteAccess = .all` and `allowedHosts` in `checkUrl`. -/
theorem checkUrl_blocked_precedence (sa : SiteAccess) (allowed blocked : List Host)
    (grants : Host → Bool) (u : Url) (hblk : hostBlocked blocked u = true) :
    checkUrlOk sa allowed blocked grants u = false := by
  simp [checkUrlOk, hblk]

/-- `checkUrl` never permits non-http(s)/non-about:blank schemes (e.g. `file:`, `about:config`). -/
theorem checkUrl_restricts_scheme {sa : SiteAccess} {allowed blocked : List Host}
    {grants : Host → Bool} {u : Url}
    (hok : checkUrlOk sa allowed blocked grants u = true) :
    u.scheme = .http ∨ u.scheme = .https ∨ u.scheme = .aboutBlank := by
  simp only [checkUrlOk, Bool.and_eq_true, Bool.or_eq_true, beq_iff_eq] at hok
  rcases hok with ⟨⟨hscheme, _⟩, _⟩
  rcases hscheme with (h | h) | h
  · exact Or.inl h
  · exact Or.inr (Or.inl h)
  · exact Or.inr (Or.inr h)

/-! ## Frame Ownership Inheritance (`ownerUrl`, `extension/background.js:352-355`) -/

inductive FrameQueryOutcome where
  | ok
  | timeoutOrError
  deriving DecidableEq, Repr

structure FormField where
  fieldId : String
  isSecret : Bool
  value : String
  deriving DecidableEq, Repr

structure Frame where
  frameId : Nat
  parentFrameId : Option Nat
  url : Url
  fields : List FormField := []
  consoleMsgs : List String := []
  scriptOutcome : FrameQueryOutcome := .ok
  deriving DecidableEq, Repr

def findFrame (frames : List Frame) (fid : Nat) : Option Frame :=
  frames.find? (fun f => f.frameId == fid)

def ownerUrlAux (frames : List Frame) : Nat → Frame → Url
  | 0, f => f.url
  | steps + 1, f =>
    if f.frameId != 0 && !isWeb f.url then
      match f.parentFrameId.bind (findFrame frames) with
      | some parent => ownerUrlAux frames steps parent
      | none => { scheme := .other, host := [] }
    else
      f.url

/-- Matches `ownerUrl(frames, f)` in `extension/background.js:352-355`. -/
def ownerUrl (frames : List Frame) (f : Frame) : Url :=
  ownerUrlAux frames 30 f

theorem ownerUrl_of_web (frames : List Frame) (f : Frame) (hweb : isWeb f.url = true) :
    ownerUrl frames f = f.url := by
  simp [ownerUrl, ownerUrlAux, hweb]

/-- A blank/srcdoc/data/blob child frame directly under a web parent inherits the parent's URL,
so blocking or disallowing the parent also blocks/disallows the blank child frame. -/
theorem ownerUrl_blank_inherits_web_parent (frames : List Frame) (child parent : Frame)
    (hchildId : child.frameId ≠ 0)
    (hnonweb : isWeb child.url = false)
    (hparId : child.parentFrameId = some parent.frameId)
    (hfind : findFrame frames parent.frameId = some parent)
    (hparWeb : isWeb parent.url = true) :
    ownerUrl frames child = parent.url := by
  have hne : (child.frameId != 0) = true := by simp [hchildId]
  simp [ownerUrl, ownerUrlAux, hne, hnonweb, hparId, hfind, hparWeb]

/-! ## Sensitive Field DOM Redaction (`stateOf`, `extension/content/content.js:240-243`) -/

inductive FieldOutput where
  | redacted (label : String) (filled : Bool)
  | plainValue (val : String)
  | empty
  deriving DecidableEq, Repr

/-- Models `stateOf` value formatting in `extension/content/content.js:240-243`. -/
def stateOfField (f : FormField) : FieldOutput :=
  if f.isSecret then
    .redacted "secret" (!f.value.isEmpty)
  else if !f.value.isEmpty then
    .plainValue f.value
  else
    .empty

def fieldOutputContainsValue (out : FieldOutput) (v : String) : Bool :=
  match out with
  | .plainValue s => s == v
  | _ => false

/-- A classified secret field's value is NEVER emitted by `stateOfField`. -/
theorem stateOf_never_leaks_secret (f : FormField) (hsec : f.isSecret = true) :
    fieldOutputContainsValue (stateOfField f) f.value = false := by
  simp [stateOfField, hsec, fieldOutputContainsValue]

/-! ## Local Socket Privacy (`assertPrivate`, `bridge/socket.mjs:12-25`) -/

structure DirStat where
  uid : Nat
  groupOrOtherWritable : Bool
  sticky : Bool
  deriving DecidableEq, Repr

def assertPrivateStep (me : Nat) (first : Bool) (d : DirStat) : Bool :=
  !((d.uid != me && d.uid != 0) || (d.groupOrOtherWritable && (first || !d.sticky)))

def assertPrivateAux (me : Nat) (first : Bool) : List DirStat → Bool
  | [] => true
  | d :: ds => assertPrivateStep me first d && assertPrivateAux me false ds

/-- Models `assertPrivate(dir)` in `bridge/socket.mjs:12-25` on the directory chain from `dir` to `/`. -/
def assertPrivateOk (me : Nat) (chain : List DirStat) : Bool :=
  assertPrivateAux me true chain

private theorem assertPrivateAux_false_forall (me : Nat) (ds : List DirStat) :
    assertPrivateAux me false ds = true →
    ∀ a ∈ ds, (a.uid = me ∨ a.uid = 0) ∧ (a.groupOrOtherWritable = true → a.sticky = true) := by
  induction ds with
  | nil => intro _ a ha; cases ha
  | cons d rest ih =>
    intro h a ha
    simp only [assertPrivateAux, Bool.and_eq_true] at h
    rcases List.mem_cons.mp ha with rfl | hmem
    · have hs := h.1
      simp only [assertPrivateStep, Bool.not_eq_true', Bool.or_eq_false_iff,
        Bool.and_eq_false_iff, bne_eq_false_iff_eq, Bool.false_or, Bool.not_eq_false'] at hs
      rcases hs with ⟨huid, hwrite⟩
      constructor
      · cases huid with
        | inl heq => exact Or.inl heq
        | inr heq => exact Or.inr heq
      · intro hw
        cases hwrite with
        | inl hfalse => rw [hw] at hfalse; cases hfalse
        | inr hsticky => exact hsticky
    · exact ih h.2 a hmem

/-- If `assertPrivateOk` passes on `dir :: ancestors`:
1. The socket directory `dir` itself is owned by `me` or `root` AND is not group/world-writable.
2. Every ancestor is owned by `me` or `root`, and if group/world-writable (e.g. `/tmp`), has sticky bit set. -/
theorem assertPrivate_guarantees_private_socket_dir (me : Nat) (dir : DirStat) (ancestors : List DirStat)
    (hok : assertPrivateOk me (dir :: ancestors) = true) :
    ((dir.uid = me ∨ dir.uid = 0) ∧ dir.groupOrOtherWritable = false) ∧
    (∀ a ∈ ancestors, (a.uid = me ∨ a.uid = 0) ∧ (a.groupOrOtherWritable = true → a.sticky = true)) := by
  simp only [assertPrivateOk, assertPrivateAux, Bool.and_eq_true] at hok
  rcases hok with ⟨hhead, htail⟩
  constructor
  · simp only [assertPrivateStep, Bool.not_eq_true', Bool.or_eq_false_iff,
      Bool.and_eq_false_iff, bne_eq_false_iff_eq, Bool.true_or, Bool.and_true] at hhead
    rcases hhead with ⟨huid, hnw⟩
    exact ⟨huid, hnw⟩
  · exact assertPrivateAux_false_forall me ancestors htail

/-! ## Windows Named-Pipe Challenge-Response (`bridge/mcp.mjs:115-151`, `bridge/host.mjs:223-227`) -/

structure PipeSession where
  token : Nat
  challenge : Nat
  proven : Bool
  requestsSent : Nat
  deriving DecidableEq, Repr

/-- Abstract HMAC verification (`proofMatches` in `bridge/connected.mjs:84-88`). -/
def proofMatches (hmac : Nat → Nat → Nat) (token challenge proof : Nat) : Bool :=
  hmac token challenge == proof

inductive PipeStep (hmac : Nat → Nat → Nat) : PipeSession → PipeSession → Prop where
  | verifyProof (s : PipeSession) (replyProof : Nat)
      (hmatch : proofMatches hmac s.token s.challenge replyProof = true) :
      PipeStep hmac s { s with proven := true }
  | sendRequest (s : PipeSession) (hproven : s.proven = true) :
      PipeStep hmac s { s with requestsSent := s.requestsSent + 1 }

inductive PipeReachable (hmac : Nat → Nat → Nat) : PipeSession → Prop where
  | init (tok ch : Nat) : PipeReachable hmac { token := tok, challenge := ch, proven := false, requestsSent := 0 }
  | step {s s' : PipeSession} : PipeReachable hmac s → PipeStep hmac s s' → PipeReachable hmac s'

/-- No tool request is ever sent on a Windows named pipe unless the pipe proved knowledge of `token`. -/
theorem windows_pipe_no_request_before_proof (hmac : Nat → Nat → Nat) {s : PipeSession}
    (hreach : PipeReachable hmac s) :
    s.requestsSent > 0 → s.proven = true := by
  induction hreach with
  | init tok ch =>
    intro hgt; cases hgt
  | step _ hstep ih =>
    cases hstep with
    | verifyProof _ _ => intro _; rfl
    | sendRequest hp => intro _; exact hp

/-! ## Local File Access Boundary (`readable` & `checkSavePath`, `bridge/mcp.mjs:203-222, 384-406`) -/

inductive Platform where
  | linux
  | darwin
  | windows
  deriving DecidableEq, Repr

structure ResolvedPath where
  isAbsolute : Bool
  segments : List String
  ext : String
  isRegularFile : Bool
  platform : Platform
  inMacLibrary : Bool
  inMacCloudDocs : Bool
  inWinAppData : Bool
  isUncOrDevice : Bool
  deriving DecidableEq, Repr

def isKernelPseudoFs (p : ResolvedPath) : Bool :=
  match p.segments with
  | "proc" :: _ | "sys" :: _ | "dev" :: _ => true
  | _ => false

def hasDotSegment (p : ResolvedPath) : Bool :=
  p.segments.any (fun s => s.startsWith ".")

def inMacLibraryGuarded (p : ResolvedPath) : Bool :=
  p.platform == .darwin && p.inMacLibrary && !p.inMacCloudDocs

def inWinGuarded (p : ResolvedPath) : Bool :=
  p.platform == .windows && (p.isUncOrDevice || p.inWinAppData)

/-- Models `readable(file, what)` in `bridge/mcp.mjs:203-222`. -/
def readableOk (allowHidden : Bool) (p : ResolvedPath) : Bool :=
  p.isAbsolute &&
  !isKernelPseudoFs p &&
  (allowHidden || !hasDotSegment p) &&
  (allowHidden || !inMacLibraryGuarded p) &&
  (allowHidden || !inWinGuarded p) &&
  p.isRegularFile

inductive SaveTool where
  | javascript
  | imageOrGif
  deriving DecidableEq, Repr

def IMAGE_SAVE_EXTS : List String := [".png", ".jpg", ".jpeg", ".gif", ".webp"]
def TEXT_SAVE_EXTS : List String := [".json", ".txt", ".csv", ".tsv", ".md", ".log"]

/-- Models `checkSavePath(file, tool)` in `bridge/mcp.mjs:384-406`. -/
def checkSavePathOk (tool : SaveTool) (p : ResolvedPath) : Bool :=
  p.isAbsolute &&
  !inMacLibraryGuarded p &&
  !inWinGuarded p &&
  match tool with
  | .javascript => TEXT_SAVE_EXTS.contains p.ext && !hasDotSegment p
  | .imageOrGif => IMAGE_SAVE_EXTS.contains p.ext

/-- With default settings (`allowHidden = false`), `readableOk` refuses kernel pseudo-fs,
dotfiles/dotdirs, macOS `~/Library` outside cloud docs, and Windows `AppData`/UNC/device paths. -/
theorem readable_default_safety {p : ResolvedPath} (hok : readableOk false p = true) :
    p.isAbsolute = true ∧
    isKernelPseudoFs p = false ∧
    hasDotSegment p = false ∧
    inMacLibraryGuarded p = false ∧
    inWinGuarded p = false ∧
    p.isRegularFile = true := by
  simp only [readableOk, Bool.and_eq_true, Bool.false_or, Bool.not_eq_true'] at hok
  rcases hok with ⟨⟨⟨⟨⟨h1, h2⟩, h3⟩, h4⟩, h5⟩, h6⟩
  exact ⟨h1, h2, h3, h4, h5, h6⟩

/-- `checkSavePathOk` refuses macOS `~/Library`, Windows `AppData`/UNC, and disallowed extensions
(plus dotfiles for `javascript`). -/
theorem savePath_safety {tool : SaveTool} {p : ResolvedPath} (hok : checkSavePathOk tool p = true) :
    p.isAbsolute = true ∧
    inMacLibraryGuarded p = false ∧
    inWinGuarded p = false ∧
    (tool = .javascript → TEXT_SAVE_EXTS.contains p.ext = true ∧ hasDotSegment p = false) ∧
    (tool = .imageOrGif → IMAGE_SAVE_EXTS.contains p.ext = true) := by
  simp only [checkSavePathOk, Bool.and_eq_true, Bool.not_eq_true'] at hok
  rcases hok with ⟨⟨⟨habs, hmac⟩, hwin⟩, hrest⟩
  refine ⟨habs, hmac, hwin, ?_, ?_⟩
  · intro hjs
    subst hjs
    simp only [Bool.and_eq_true, Bool.not_eq_true'] at hrest
    exact hrest
  · intro himg
    subst himg
    exact hrest

end Foxreach
