Output docs/failover.gif

Set Shell "bash"
Set FontSize 15
Set Width 1180
Set Height 860
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set PlaybackSpeed 2.0

Hide
Type "clear" Enter
Show

Type "make test-failover" Sleep 600ms Enter
Wait+Screen@180s /PASS|FAIL/
Sleep 14s
