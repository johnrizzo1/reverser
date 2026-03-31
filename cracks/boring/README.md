# boring

*Yawn.*

Just another crackme. Nothing special. You'll probably solve it in five minutes
and wonder why you even bothered.

## What Is This?

A binary. It asks for a key. You either know it or you don't.

```
$ ./boring
Enter the key: hunter2
Access denied
```

See? Boring.

## Rules

1. Find the key
2. Enter the key
3. That's it

- **Flag format:** `FLAG{...}` (38 characters, printable ASCII)
- **Platform:** Linux x86-64
- **Dependencies:** None worth mentioning
- **Network:** Not required. Everything you need is right there in the binary.

## Getting Started

```
$ chmod +x boring
$ ./boring
Enter the key: _
```

Now figure out the rest.

## Hints

There are no hints. It's too boring for hints.

...

OK fine. One.

> `strings` won't help you.

## FAQ

**Q: The binary just says "Access denied" no matter what I type.**
A: Sounds like you're typing the wrong thing.

**Q: It says "Access denied" even when I'm debugging it.**
A: Huh. Weird. Probably nothing.

**Q: I found a flag in the binary with `strings`!**
A: Did you try it? How did that go?

**Q: Is this really just a boring crackme?**
A: What were you expecting? It says so right in the name.

**Q: I solved it.**
A: Congratulations. Try not to let the excitement overwhelm you.

## Difficulty

```
[                    ] 0%  Boring
```

Good luck. Or don't. It's fine either way.
