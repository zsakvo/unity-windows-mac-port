/*
 * libTolk.dylib - no-op stand-in for Tolk (Windows screen-reader bridge).
 * Tolk has no macOS backend (NVDA/Narrator/ZDSR/SAPI are Windows-only), so this
 * stub reports "no screen reader present", which is exactly the state the game
 * already handles on a machine without assistive tech.
 * bool-returning entry points return int (0/1) so the result is correct whether
 * the managed side marshals as a 1-byte or 4-byte boolean.
 */
#include <stdint.h>

typedef uint16_t wchar16; /* Mono marshals LPWStr as UTF-16 code units */

void Tolk_Load(void) {}
int  Tolk_IsLoaded(void) { return 0; }
void Tolk_Unload(void) {}
void Tolk_TrySAPI(int trySAPI) { (void)trySAPI; }
void Tolk_PreferSAPI(int preferSAPI) { (void)preferSAPI; }
const wchar16 *Tolk_DetectScreenReader(void) { return 0; }
int  Tolk_HasSpeech(void) { return 0; }
int  Tolk_HasBraille(void) { return 0; }
int  Tolk_Output(const wchar16 *str, int interrupt) { (void)str; (void)interrupt; return 0; }
int  Tolk_Speak(const wchar16 *str, int interrupt) { (void)str; (void)interrupt; return 0; }
int  Tolk_Braille(const wchar16 *str) { (void)str; return 0; }
int  Tolk_IsSpeaking(void) { return 0; }
int  Tolk_Silence(void) { return 1; }
