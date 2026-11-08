using System.ComponentModel;
using System.Windows.Media;

namespace PeekSetup.ViewModels
{
    public class QualityTickBarViewModel : INotifyPropertyChanged
    {
        private int _index;
        private bool _isActive;
        private Brush _fillBrush = Brushes.Transparent;

        public int Index
        {
            get => _index;
            set
            {
                if (_index != value)
                {
                    _index = value;
                    OnPropertyChanged(nameof(Index));
                }
            }
        }

        public bool IsActive
        {
            get => _isActive;
            set
            {
                if (_isActive != value)
                {
                    _isActive = value;
                    OnPropertyChanged(nameof(IsActive));
                }
            }
        }

        public Brush FillBrush
        {
            get => _fillBrush;
            set
            {
                if (_fillBrush != value)
                {
                    _fillBrush = value;
                    OnPropertyChanged(nameof(FillBrush));
                }
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
