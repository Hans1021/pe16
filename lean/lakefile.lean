import Lake
open Lake DSL

package pe16

@[default_target]
lean_lib Pe16

@[default_target]
lean_exe crosscheck where
  root := `Main
