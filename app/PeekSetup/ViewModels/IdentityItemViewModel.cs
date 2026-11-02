using System;
using System.Collections.Generic;
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
        private readonly Func<IdentityItemViewModel, Task>? _onActivate;

        private string _displayName = string.Empty;
        private string _editingName = string.Empty;
        private bool _isEditingName;
        private bool _isEnabled = true;
        private bool _isAuthorized = true;
        private int? _qualityPercentage;
        private List<double> _poseQualities = new();
        private bool _suppressToggleCallback;

        public static readonly Brush GreenBrush = new SolidColorBrush(Color.FromRgb(0x30, 0xD1, 0x58));
        public static readonly Brush AmberBrush = new SolidColorBrush(Color.FromRgb(0xFF, 0x95, 0x00));
        public static readonly Brush RedBrush = new SolidColorBrush(Color.FromRgb(0xFF, 0x45, 0x3A));
        public static readonly Brush GrayBrush = new SolidColorBrush(Color.FromRgb(0x8E, 0x8E, 0x93));

        static IdentityItemViewModel()
        {
            GreenBrush.Freeze();
            AmberBrush.Freeze();
            RedBrush.Freeze();
            GrayBrush.Freeze();
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
                    if (!_suppressToggleCallback)
                    {
                        _onToggle?.Invoke(this, value);
                    }
                }
            }
        }

        public void SetEnabledSilently(bool enabled)
        {
            _suppressToggleCallback = true;
            try
            {
                IsEnabled = enabled;
            }
            finally
            {
                _suppressToggleCallback = false;
            }
        }

        public bool IsAuthorized
        {
            get => _isAuthorized;
            set
            {
                if (_isAuthorized != value)
                {
                    _isAuthorized = value;
                    OnPropertyChanged(nameof(IsAuthorized));
                    OnPropertyChanged(nameof(IsPending));
                    OnPropertyChanged(nameof(StatusBadgeText));
                }
            }
        }

        public bool IsPending => !IsAuthorized;
        public string StatusBadgeText => IsAuthorized ? "Active" : "Not activated";

        public int? QualityPercentage
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

        public IReadOnlyList<double> PoseQualities
        {
            get => _poseQualities;
            set
            {
                _poseQualities = value != null ? new List<double>(value) : new List<double>();
                OnPropertyChanged(nameof(PoseQualities));
                UpdateQualityBars();
            }
        }

        public string CaptureQualityText => QualityPercentage.HasValue
            ? $"Capture quality • {QualityPercentage.Value}%"
            : "Quality unknown";

        public ObservableCollection<QualityTickBarViewModel> QualityBars { get; } = new();

        public ICommand RecaptureCommand { get; }
        public ICommand DeleteCommand { get; }
        public ICommand ActivateCommand { get; }
        public ICommand BeginEditCommand { get; }
        public ICommand SaveEditCommand { get; }
        public ICommand CancelEditCommand { get; }

        public IdentityItemViewModel(
            string profileId,
            string displayName,
            bool isEnabled,
            int? qualityPercentage,
            Func<IdentityItemViewModel, bool, Task>? onToggle = null,
            Func<IdentityItemViewModel, Task>? onRecapture = null,
            Func<IdentityItemViewModel, Task>? onDelete = null,
            Func<IdentityItemViewModel, string, Task>? onRename = null)
            : this(profileId, displayName, isEnabled, isAuthorized: true, qualityPercentage, null, onToggle, onRecapture, onDelete, onRename, null)
        {
        }

        public IdentityItemViewModel(
            string profileId,
            string displayName,
            bool isEnabled,
            bool isAuthorized,
            int? qualityPercentage,
            IReadOnlyList<double>? poseQualities = null,
            Func<IdentityItemViewModel, bool, Task>? onToggle = null,
            Func<IdentityItemViewModel, Task>? onRecapture = null,
            Func<IdentityItemViewModel, Task>? onDelete = null,
            Func<IdentityItemViewModel, string, Task>? onRename = null,
            Func<IdentityItemViewModel, Task>? onActivate = null)
        {
            ProfileId = profileId;
            _displayName = displayName;
            _editingName = displayName;
            _isEnabled = isEnabled;
            _isAuthorized = isAuthorized;
            _qualityPercentage = qualityPercentage;
            if (poseQualities != null)
            {
                _poseQualities = new List<double>(poseQualities);
            }
            _onToggle = onToggle;
            _onRecapture = onRecapture;
            _onDelete = onDelete;
            _onRename = onRename;
            _onActivate = onActivate;

            RecaptureCommand = new RelayCommand(() => _onRecapture?.Invoke(this));
            DeleteCommand = new RelayCommand(() => _onDelete?.Invoke(this));
            ActivateCommand = new RelayCommand(() => _onActivate?.Invoke(this));
            BeginEditCommand = new RelayCommand(BeginEdit);
            SaveEditCommand = new AsyncRelayCommand(SaveEditAsync);
            CancelEditCommand = new RelayCommand(CancelEdit);

            UpdateQualityBars();
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

        public static Brush GetPoseBrush(double score)
        {
            if (score < 0) return GrayBrush;
            if (score >= 0.70) return GreenBrush;
            if (score >= 0.50) return AmberBrush;
            return RedBrush;
        }

        private void UpdateQualityBars()
        {
            QualityBars.Clear();

            if (_poseQualities != null && _poseQualities.Count > 0)
            {
                for (int i = 0; i < 9; i++)
                {
                    double score = i < _poseQualities.Count ? _poseQualities[i] : -1.0;
                    QualityBars.Add(new QualityTickBarViewModel
                    {
                        Index = i,
                        IsActive = true,
                        FillBrush = GetPoseBrush(score)
                    });
                }
            }
            else
            {
                Brush defaultBrush = QualityPercentage.HasValue
                    ? GetPoseBrush(QualityPercentage.Value / 100.0)
                    : GrayBrush;

                for (int i = 0; i < 9; i++)
                {
                    QualityBars.Add(new QualityTickBarViewModel
                    {
                        Index = i,
                        IsActive = true,
                        FillBrush = defaultBrush
                    });
                }
            }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged(string name) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
