using System;
using System.Windows;
using System.Windows.Media;
using PeekSetup.Services;
using Xunit;

namespace PeekSetup.Tests
{
    public class ThemeManagerTests
    {
        [Fact]
        public void IsSystemDarkTheme_DoesNotThrow()
        {
            // Verify registry reading does not throw and returns a boolean
            bool isDark = ThemeManager.IsSystemDarkTheme();
            Assert.True(isDark || !isDark);
        }

        [Fact]
        public void ApplyTheme_PopulatesExpectedResources()
        {
            var res = new ResourceDictionary();

            // Test Light Mode
            ThemeManager.ApplyTheme(isDark: false, res);
            var lightBg = res["BgDarkColor"] as Color?;
            Assert.NotNull(lightBg);
            Assert.Equal(Color.FromRgb(0xF5, 0xF5, 0xF7), lightBg.Value);

            var lightText = res["TextPrimaryColor"] as Color?;
            Assert.NotNull(lightText);
            Assert.Equal(Color.FromRgb(0x1D, 0x1D, 0x1F), lightText.Value);

            // Test Dark Mode
            ThemeManager.ApplyTheme(isDark: true, res);
            var darkBg = res["BgDarkColor"] as Color?;
            Assert.NotNull(darkBg);
            Assert.Equal(Color.FromRgb(0x16, 0x16, 0x17), darkBg.Value);

            var darkText = res["TextPrimaryColor"] as Color?;
            Assert.NotNull(darkText);
            Assert.Equal(Color.FromRgb(0xF5, 0xF5, 0xF7), darkText.Value);
        }
    }
}
