using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Threading.Tasks;
using System.Windows.Input;
using System.Windows.Media;

namespace PeekSetup.ViewModels
{
    public class IdentityItemViewModel : INotifyPropertyChanged
    {
        private readonly Func<IdentityItemViewModel, bool, Task>? _onToggle;
        private readonly Func<IdentityItemViewModel, Task>? _onRecapture;
        private readonly Func<IdentityItemViewModel, Task>? _onDelete;
        private readonly Func<IdentityItemViewModel, string, Task>? _onRename;

        private string _displayName = string.Empty;
        private string _editingName = string.Empty;
        private bool _isEditingName;
        private bool _isEnabled = true;
        private int _qualityPercentage = 85;

        private static readonly Brush InactiveBrush = new SolidColorBrush(Color.FromRgb(0x2C, 0x2E, 0x35));

        // 20-bar Apple/Glance-style color spectrum:
        // Bars 0-3: #FF5B5B (coral/red)
        // Bars 4-7: #FF8C42 (coral/orange)
        // Bars 8-11: #FFA043 (orange)
        // Bars 12-15: #FFD147 (gold/yellow)
        // Bars 16-19: #4CD964 (emerald green)
        private static readonly Brush[] GradientBrushes = new Brush[20]
        {
            new SolidColorBrush(Color.FromRgb(0xEB, 0x5D, 0x5D)),
            new SolidColorBrush(Color.FromRgb(0xEE, 0x66, 0x58)),
            new SolidColorBrush(Color.FromRgb(0xF0, 0x70, 0x52)),
            new SolidColorBrush(Color.FromRgb(0xF3, 0x7A, 0x4D)),

            new SolidColorBrush(Color.FromRgb(0xF5, 0x85, 0x48)),
            new SolidColorBrush(Color.FromRgb(0xF7, 0x90, 0x43)),
            new SolidColorBrush(Color.FromRgb(0xFA, 0x9B, 0x3E)),
            new SolidColorBrush(Color.FromRgb(0xFC, 0xA6, 0x3A)),

            new SolidColorBrush(Color.FromRgb(0xFE, 0xB2, 0x37)),
            new SolidColorBrush(Color.FromRgb(0xFF, 0xBD, 0x36)),
            new SolidColorBrush(Color.FromRgb(0xFF, 0xC7, 0x37)),
            new SolidColorBrush(Color.FromRgb(0xFF, 0xD2, 0x3A)),

            new SolidColorBrush(Color.FromRgb(0xF5, 0xD6, 0x42)),
            new SolidColorBrush(Color.FromRgb(0xEB, 0xD9, 0x4B)),
            new SolidColorBrush(Color.FromRgb(0xDD, 0xDC, 0x55)),
            new SolidColorBrush(Color.FromRgb(0xCE, 0xDE, 0x5E)),

            new SolidColorBrush(Color.FromRgb(0xBD, 0xDE, 0x66)),
            new SolidColorBrush(Color.FromRgb(0xA6, 0xDD, 0x6D)),
            new SolidColorBrush(Color.FromRgb(0x8C, 0xDB, 0x73)),
            new SolidColorBrush(Color.FromRgb(0x4C, 0xD9, 0x64)),
        };

        static IdentityItemViewModel()
        {
            InactiveBrush.Freeze();
            foreach (var b in GradientBrushes)
            {
                b.Freeze();
            }
        }

        public string ProfileId { get; set; } = string.Empty;

        public string DisplayName
        {
            get => _displayName;
            set
            {
                if (_displayName != value)
                {
                    _displayName = value;
                    OnPropertyChanged(nameof(DisplayName));
                }
            }
        }

        public string EditingName
        {
            get => _editingName;
            set
            {
                if (_editingName != value)
                {
                    _editingName = value;
                    OnPropertyChanged(nameof(EditingName));
                }
            }
        }

        public bool IsEditingName
        {
            get => _isEditingName;
            set
            {
                if (_isEditingName != value)
                {
                    _isEditingName = value;
                    OnPropertyChanged(nameof(IsEditingName));
                }
            }
        }

        public bool IsEnabled
        {
            get => _isEnabled;
            set
            {
                if (_isEnabled != value)
                {
                    _isEnabled = value;
                    OnPropertyChanged(nameof(IsEnabled));
                    _onToggle?.Invoke(this, value);
                }
            }
        }

        public int QualityPercentage
        {
            get => _qualityPercentage;
            set
            {
                if (_qualityPercentage != value)
                {
                    _qualityPercentage = value;
                    OnPropertyChanged(nameof(QualityPercentage));
                    OnPropertyChanged(nameof(CaptureQualityText));
                    UpdateQualityBars();
                }
            }
        }

        public string CaptureQualityText => $"Capture quality • {QualityPercentage}%";

        public ObservableCollection<QualityTickBarViewModel> QualityBars { get; } = new();

        public ICommand RecaptureCommand { get; }
        public ICommand DeleteCommand { get; }
        public ICommand BeginEditCommand { get; }
        public ICommand SaveEditCommand { get; }
        public ICommand CancelEditCommand { get; }

        public IdentityItemViewModel(
            string profileId,
            string displayName,
            bool isEnabled,
            int qualityPercentage,
            Func<IdentityItemViewModel, bool, Task>? onToggle = null,
            Func<IdentityItemViewModel, Task>? onRecapture = null,
            Func<IdentityItemViewModel, Task>? onDelete = null,
            Func<IdentityItemViewModel, string, Task>? onRename = null)
        {
            ProfileId = profileId;
            _displayName = displayName;
            _editingName = displayName;
            _isEnabled = isEnabled;
            _qualityPercentage = qualityPercentage;
            _onToggle = onToggle;
            _onRecapture = onRecapture;
            _onDelete = onDelete;
            _onRename = onRename;

            RecaptureCommand = new RelayCommand(() => _onRecapture?.Invoke(this));
            DeleteCommand = new RelayCommand(() => _onDelete?.Invoke(this));
            BeginEditCommand = new RelayCommand(BeginEdit);
            SaveEditCommand = new AsyncRelayCommand(SaveEditAsync);
            CancelEditCommand = new RelayCommand(CancelEdit);

            InitializeQualityBars();
        }

        public void BeginEdit()
        {
            EditingName = DisplayName;
            IsEditingName = true;
        }

        public async Task SaveEditAsync()
        {
            var trimmed = EditingName?.Trim() ?? string.Empty;
            if (!string.IsNullOrWhiteSpace(trimmed))
            {
                string oldName = DisplayName;
                DisplayName = trimmed;
                if (_onRename != null && trimmed != oldName)
                {
                    await _onRename(this, trimmed).ConfigureAwait(false);
                }
            }
            else
            {
                EditingName = DisplayName;
            }
            IsEditingName = false;
        }

        public void CancelEdit()
        {
            EditingName = DisplayName;
            IsEditingName = false;
        }

        private void InitializeQualityBars()
        {
            QualityBars.Clear();
            int activeCount = (int)Math.Round((QualityPercentage / 100.0) * 20.0);
            for (int i = 0; i < 20; i++)
            {
                bool isActive = i < activeCount;
                QualityBars.Add(new QualityTickBarViewModel
                {
                    Index = i,
                    IsActive = isActive,
                    FillBrush = isActive ? GradientBrushes[i] : InactiveBrush
                });
            }
        }

        private void UpdateQualityBars()
        {
            int activeCount = (int)Math.Round((QualityPercentage / 100.0) * 20.0);
            for (int i = 0; i < QualityBars.Count; i++)
            {
                bool isActive = i < activeCount;
                QualityBars[i].IsActive = isActive;
                QualityBars[i].FillBrush = isActive ? GradientBrushes[i] : InactiveBrush;
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
