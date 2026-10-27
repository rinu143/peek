using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;

namespace PeekSetup.ViewModels
{
    public class TickViewModel : INotifyPropertyChanged
    {
        private const double CenterX = 185.0;
        private const double CenterY = 185.0;
        private const double InnerRadius = 155.0;
        private const double BaseLength = 14.0;

        private static readonly SolidColorBrush IdleBrush;
        private static readonly SolidColorBrush CompletedBrush;
        private static readonly SolidColorBrush GreenBrush;
        private static readonly SolidColorBrush[] LobeBrushes;

        static TickViewModel()
        {
            IdleBrush = new SolidColorBrush(Color.FromRgb(255, 255, 255));
            IdleBrush.Freeze();

            // Soft blue for completed sectors (~35%)
            CompletedBrush = new SolidColorBrush(Color.FromArgb(128, 10, 132, 255));
            CompletedBrush.Freeze();

            // Apple Green (#30D158)
            GreenBrush = new SolidColorBrush(Color.FromRgb(48, 209, 88));
            GreenBrush.Freeze();

            // 101-step pre-allocated gradient from White (#FFFFFF) to Apple Blue (#0A84FF)
            LobeBrushes = new SolidColorBrush[101];
            for (int i = 0; i <= 100; i++)
            {
                double t = i / 100.0;
                byte r = (byte)Math.Round(255.0 - (255.0 - 10.0) * t);
                byte g = (byte)Math.Round(255.0 - (255.0 - 132.0) * t);
                byte b = 255;
                var brush = new SolidColorBrush(Color.FromRgb(r, g, b));
                brush.Freeze();
                LobeBrushes[i] = brush;
            }
        }

        private readonly double _cos;
        private readonly double _sin;

        public int Index { get; }
        public double Angle { get; }

        private double _x1;
        public double X1
        {
            get => _x1;
            private set { if (Math.Abs(_x1 - value) > 0.001) { _x1 = value; OnPropertyChanged(); } }
        }

        private double _y1;
        public double Y1
        {
            get => _y1;
            private set { if (Math.Abs(_y1 - value) > 0.001) { _y1 = value; OnPropertyChanged(); } }
        }

        private double _x2;
        public double X2
        {
            get => _x2;
            private set { if (Math.Abs(_x2 - value) > 0.001) { _x2 = value; OnPropertyChanged(); } }
        }

        private double _y2;
        public double Y2
        {
            get => _y2;
            private set { if (Math.Abs(_y2 - value) > 0.001) { _y2 = value; OnPropertyChanged(); } }
        }

        private double _intensity;
        public double Intensity
        {
            get => _intensity;
            private set { if (Math.Abs(_intensity - value) > 0.001) { _intensity = value; OnPropertyChanged(); } }
        }

        private bool _isCompleted;
        public bool IsCompleted
        {
            get => _isCompleted;
            private set { if (_isCompleted != value) { _isCompleted = value; OnPropertyChanged(); } }
        }

        private Brush _strokeBrush = IdleBrush;
        public Brush StrokeBrush
        {
            get => _strokeBrush;
            private set { if (!ReferenceEquals(_strokeBrush, value)) { _strokeBrush = value; OnPropertyChanged(); } }
        }

        private double _opacity = 0.85;
        public double Opacity
        {
            get => _opacity;
            private set { if (Math.Abs(_opacity - value) > 0.001) { _opacity = value; OnPropertyChanged(); } }
        }

        public TickViewModel(int index, double angle)
        {
            Index = index;
            Angle = angle;

            double rad = angle * Math.PI / 180.0;
            _cos = Math.Cos(rad);
            _sin = Math.Sin(rad);

            _x1 = CenterX + InnerRadius * _cos;
            _y1 = CenterY + InnerRadius * _sin;
            _x2 = CenterX + (InnerRadius + BaseLength) * _cos;
            _y2 = CenterY + (InnerRadius + BaseLength) * _sin;
        }

        public void Update(double liveIntensity, bool isCompleted, bool isGreenSweep)
        {
            Intensity = liveIntensity;
            IsCompleted = isCompleted;

            if (isGreenSweep)
            {
                StrokeBrush = GreenBrush;
                Opacity = 1.0;
                double len = BaseLength * 1.15;
                X2 = CenterX + (InnerRadius + len) * _cos;
                Y2 = CenterY + (InnerRadius + len) * _sin;
                return;
            }

            if (liveIntensity > 0.005)
            {
                int idx = (int)Math.Round(Math.Clamp(liveIntensity, 0.0, 1.0) * 100.0);
                StrokeBrush = LobeBrushes[idx];
                Opacity = 0.85 + 0.15 * Math.Clamp(liveIntensity, 0.0, 1.0);
                double len = BaseLength * (1.0 + 0.20 * Math.Clamp(liveIntensity, 0.0, 1.0));
                X2 = CenterX + (InnerRadius + len) * _cos;
                Y2 = CenterY + (InnerRadius + len) * _sin;
            }
            else if (isCompleted)
            {
                StrokeBrush = CompletedBrush;
                Opacity = 0.70;
                X2 = CenterX + (InnerRadius + BaseLength) * _cos;
                Y2 = CenterY + (InnerRadius + BaseLength) * _sin;
            }
            else
            {
                StrokeBrush = IdleBrush;
                Opacity = 0.85;
                X2 = CenterX + (InnerRadius + BaseLength) * _cos;
                Y2 = CenterY + (InnerRadius + BaseLength) * _sin;
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
