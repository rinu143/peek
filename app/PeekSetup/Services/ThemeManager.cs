using System;
using System.Windows;
using System.Windows.Media;
using Microsoft.Win32;

namespace PeekSetup.Services
{
    /// <summary>
    /// Detects Windows system theme (AppsUseLightTheme) and dynamically updates
    /// application colors and brushes to match Apple-grade Light or Dark minimalism.
    /// </summary>
    public static class ThemeManager
    {
        public static bool IsSystemDarkTheme()
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
                var val = key?.GetValue("AppsUseLightTheme");
                if (val is int i)
                {
                    return i == 0; // 0 = Dark, 1 = Light
                }
            }
            catch
            {
                // Fallback gracefully
            }
            return false; // Default to clean light mode if indeterminate
        }

        public static void InitializeTheme()
        {
            ApplyTheme(IsSystemDarkTheme());

            try
            {
                // Listen to live Windows system theme changes
                SystemEvents.UserPreferenceChanged += (s, e) =>
                {
                    if (e.Category == UserPreferenceCategory.General || e.Category == UserPreferenceCategory.VisualStyle)
                    {
                        Application.Current?.Dispatcher.Invoke(() =>
                        {
                            ApplyTheme(IsSystemDarkTheme());
                        });
                    }
                };
            }
            catch
            {
                // Non-fatal if event hook fails
            }
        }

        public static void ApplyTheme(bool isDark, ResourceDictionary? targetResources = null)
        {
            var res = targetResources ?? Application.Current?.Resources;
            if (res == null) return;

            if (isDark)
            {
                // ==========================================
                // APPLE DARK MODE (MINIMAL & CALM)
                // ==========================================
                SetColor(res, "BgDarkColor", Color.FromRgb(0x16, 0x16, 0x17));
                SetColor(res, "SurfaceDarkColor", Color.FromRgb(0x1E, 0x1E, 0x1F));
                SetColor(res, "CardBgColor", Color.FromRgb(0x23, 0x23, 0x26));
                SetColor(res, "CardBorderColor", Color.FromArgb(0x2E, 0xFF, 0xFF, 0xFF)); // 18% white rim
                SetColor(res, "CardHoverColor", Color.FromRgb(0x2C, 0x2C, 0x2E));
                SetColor(res, "PillBgColor", Color.FromRgb(0x28, 0x28, 0x2A));

                SetColor(res, "TextPrimaryColor", Color.FromRgb(0xF5, 0xF5, 0xF7));
                SetColor(res, "TextSecondaryColor", Color.FromRgb(0x86, 0x86, 0x8B));
                SetColor(res, "TextMutedColor", Color.FromRgb(0x54, 0x54, 0x58));

                // Apple CTA in dark: Solid crisp white pill with black text
                SetColor(res, "BtnPrimaryBgColor", Color.FromRgb(0xF5, 0xF5, 0xF7));
                SetColor(res, "BtnPrimaryFgColor", Color.FromRgb(0x16, 0x16, 0x17));
                SetColor(res, "BtnPrimaryHoverColor", Color.FromRgb(0xFF, 0xFF, 0xFF));

                // Secondary pill
                SetColor(res, "BtnSecondaryBgColor", Color.FromArgb(0x20, 0xFF, 0xFF, 0xFF));
                SetColor(res, "BtnSecondaryBorderColor", Color.FromArgb(0x33, 0xFF, 0xFF, 0xFF));
                SetColor(res, "BtnSecondaryFgColor", Color.FromRgb(0xF5, 0xF5, 0xF7));

                // System Blue accent
                SetColor(res, "AccentColor", Color.FromRgb(0x0A, 0x84, 0xFF));
                SetColor(res, "AccentHoverColor", Color.FromRgb(0x40, 0x9C, 0xFF));
                SetColor(res, "AccentGlowColor", Color.FromRgb(0x64, 0xD2, 0xFF));

                // Semantic statuses (Apple system colors)
                SetColor(res, "SuccessColor", Color.FromRgb(0x30, 0xD1, 0x58));
                SetColor(res, "SuccessBgColor", Color.FromRgb(0x12, 0x2B, 0x1A));
                SetColor(res, "SuccessBorderColor", Color.FromRgb(0x24, 0x6E, 0x36));

                SetColor(res, "WarningColor", Color.FromRgb(0xFF, 0x9F, 0x0A));
                SetColor(res, "WarningBgColor", Color.FromRgb(0x2E, 0x20, 0x0A));
                SetColor(res, "WarningBorderColor", Color.FromRgb(0x73, 0x4D, 0x12));

                SetColor(res, "DangerColor", Color.FromRgb(0xFF, 0x45, 0x3A));
                SetColor(res, "DangerBgColor", Color.FromRgb(0x2E, 0x12, 0x12));
                SetColor(res, "DangerBorderColor", Color.FromRgb(0x73, 0x22, 0x22));

                SetColor(res, "InputBgColor", Color.FromRgb(0x1F, 0x1F, 0x21));
                SetColor(res, "InputBorderColor", Color.FromArgb(0x33, 0xFF, 0xFF, 0xFF));
            }
            else
            {
                // ==========================================
                // APPLE LIGHT MODE (CLEAN & AIRY)
                // ==========================================
                SetColor(res, "BgDarkColor", Color.FromRgb(0xF5, 0xF5, 0xF7));
                SetColor(res, "SurfaceDarkColor", Color.FromRgb(0xEB, 0xEB, 0xEE));
                SetColor(res, "CardBgColor", Color.FromRgb(0xFF, 0xFF, 0xFF));
                SetColor(res, "CardBorderColor", Color.FromRgb(0xE5, 0xE5, 0xEA));
                SetColor(res, "CardHoverColor", Color.FromRgb(0xF2, 0xF2, 0xF7));
                SetColor(res, "PillBgColor", Color.FromRgb(0xE5, 0xE5, 0xEA));

                SetColor(res, "TextPrimaryColor", Color.FromRgb(0x1D, 0x1D, 0x1F));
                SetColor(res, "TextSecondaryColor", Color.FromRgb(0x86, 0x86, 0x8B));
                SetColor(res, "TextMutedColor", Color.FromRgb(0xAE, 0xAE, 0xB2));

                // Apple CTA in light: Solid dark slate pill with crisp white text
                SetColor(res, "BtnPrimaryBgColor", Color.FromRgb(0x1D, 0x1D, 0x1F));
                SetColor(res, "BtnPrimaryFgColor", Color.FromRgb(0xFF, 0xFF, 0xFF));
                SetColor(res, "BtnPrimaryHoverColor", Color.FromRgb(0x3A, 0x3A, 0x3C));

                // Secondary pill
                SetColor(res, "BtnSecondaryBgColor", Color.FromRgb(0xF2, 0xF2, 0xF7));
                SetColor(res, "BtnSecondaryBorderColor", Color.FromRgb(0xD1, 0xD1, 0xD6));
                SetColor(res, "BtnSecondaryFgColor", Color.FromRgb(0x1D, 0x1D, 0x1F));

                // System Blue accent
                SetColor(res, "AccentColor", Color.FromRgb(0x00, 0x7A, 0xFF));
                SetColor(res, "AccentHoverColor", Color.FromRgb(0x00, 0x62, 0xCC));
                SetColor(res, "AccentGlowColor", Color.FromRgb(0x00, 0x7A, 0xFF));

                // Semantic statuses
                SetColor(res, "SuccessColor", Color.FromRgb(0x34, 0xC7, 0x59));
                SetColor(res, "SuccessBgColor", Color.FromRgb(0xE8, 0xF8, 0xEE));
                SetColor(res, "SuccessBorderColor", Color.FromRgb(0xA3, 0xE5, 0xB6));

                SetColor(res, "WarningColor", Color.FromRgb(0xFF, 0x95, 0x00));
                SetColor(res, "WarningBgColor", Color.FromRgb(0xFF, 0xF5, 0xE6));
                SetColor(res, "WarningBorderColor", Color.FromRgb(0xFF, 0xCC, 0x80));

                SetColor(res, "DangerColor", Color.FromRgb(0xFF, 0x3B, 0x30));
                SetColor(res, "DangerBgColor", Color.FromRgb(0xFF, 0xEB, 0xEA));
                SetColor(res, "DangerBorderColor", Color.FromRgb(0xFF, 0x9E, 0x99));

                SetColor(res, "InputBgColor", Color.FromRgb(0xFF, 0xFF, 0xFF));
                SetColor(res, "InputBorderColor", Color.FromRgb(0xD1, 0xD1, 0xD6));
            }

            // Refresh SolidColorBrushes for dynamic bindings
            UpdateBrush(res, "BgDarkBrush", "BgDarkColor");
            UpdateBrush(res, "SurfaceDarkBrush", "SurfaceDarkColor");
            UpdateBrush(res, "CardBgBrush", "CardBgColor");
            UpdateBrush(res, "CardBorderBrush", "CardBorderColor");
            UpdateBrush(res, "CardHoverBrush", "CardHoverColor");
            UpdateBrush(res, "PillBgBrush", "PillBgColor");

            UpdateBrush(res, "TextPrimaryBrush", "TextPrimaryColor");
            UpdateBrush(res, "TextSecondaryBrush", "TextSecondaryColor");
            UpdateBrush(res, "TextMutedBrush", "TextMutedColor");

            UpdateBrush(res, "BtnPrimaryBgBrush", "BtnPrimaryBgColor");
            UpdateBrush(res, "BtnPrimaryFgBrush", "BtnPrimaryFgColor");
            UpdateBrush(res, "BtnPrimaryHoverBrush", "BtnPrimaryHoverColor");

            UpdateBrush(res, "BtnSecondaryBgBrush", "BtnSecondaryBgColor");
            UpdateBrush(res, "BtnSecondaryBorderBrush", "BtnSecondaryBorderColor");
            UpdateBrush(res, "BtnSecondaryFgBrush", "BtnSecondaryFgColor");

            UpdateBrush(res, "AccentBrush", "AccentColor");
            UpdateBrush(res, "AccentHoverBrush", "AccentHoverColor");
            UpdateBrush(res, "AccentGlowBrush", "AccentGlowColor");

            UpdateBrush(res, "SuccessBrush", "SuccessColor");
            UpdateBrush(res, "SuccessBgBrush", "SuccessBgColor");
            UpdateBrush(res, "SuccessBorderBrush", "SuccessBorderColor");

            UpdateBrush(res, "WarningBrush", "WarningColor");
            UpdateBrush(res, "WarningBgBrush", "WarningBgColor");
            UpdateBrush(res, "WarningBorderBrush", "WarningBorderColor");

            UpdateBrush(res, "DangerBrush", "DangerColor");
            UpdateBrush(res, "DangerBgBrush", "DangerBgColor");
            UpdateBrush(res, "DangerBorderBrush", "DangerBorderColor");

            UpdateBrush(res, "InputBgBrush", "InputBgColor");
            UpdateBrush(res, "InputBorderBrush", "InputBorderColor");

            // Dynamic AppLogoSource switching (white glyph for dark mode, dark glyph for light mode)
            string logoKey = isDark ? "AppLogoWhite" : "AppLogoDark";
            if (Application.Current?.Resources != null && Application.Current.Resources.Contains(logoKey))
            {
                res["AppLogoSource"] = Application.Current.Resources[logoKey];
            }
            else if (res.Contains(logoKey))
            {
                res["AppLogoSource"] = res[logoKey];
            }
            else
            {
                try
                {
                    string packUri = isDark
                        ? "pack://application:,,,/assets/frame_search_1.png"
                        : "pack://application:,,,/assets/frame_search_1_dark.png";
                    var bmp = new System.Windows.Media.Imaging.BitmapImage();
                    bmp.BeginInit();
                    bmp.UriSource = new Uri(packUri, UriKind.RelativeOrAbsolute);
                    bmp.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                    bmp.EndInit();
                    bmp.Freeze();
                    res["AppLogoSource"] = bmp;
                }
                catch
                {
                    // Fallback for headless testing
                }
            }
        }

        private static void SetColor(ResourceDictionary res, string key, Color color)
        {
            res[key] = color;
        }

        private static void UpdateBrush(ResourceDictionary res, string brushKey, string colorKey)
        {
            if (res.Contains(colorKey) && res[colorKey] is Color c)
            {
                var brush = new SolidColorBrush(c);
                brush.Freeze();
                res[brushKey] = brush;
            }
        }
    }
}
